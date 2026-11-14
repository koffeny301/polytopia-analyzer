/*
 * polyread_dump.c — веха 6.2: снимки живого состояния матча.
 *
 * Читает изнутри процесса игры объект GameState и пишет JSON-снимок в inbox.
 * Только чтение: ни одного set-вызова, никаких вызовов методов игры.
 *
 * Включается переменной окружения:
 *   POLYREAD_DUMP=1            → ~/.local/share/polytopia-analyzer/inbox/
 *   POLYREAD_DUMP=/путь        → указанная папка
 *   POLYREAD_DUMP_FIND=1       → только найти GameState и напечатать путь (без снимков)
 *   POLYREAD_DUMP_INTERVAL=300 → период опроса, мс (по умолчанию 300)
 *
 * Защита от падений: каждое чтение памяти объекта обёрнуто в sigsetjmp и
 * SIGSEGV/SIGBUS-обработчик, который действует только для нашего потока.
 * Чужой SIGSEGV (игры) передаётся прежнему обработчику как есть.
 *
 * Грабли, из-за которых сделано именно так (см. shim/README.md):
 *   - il2cpp_* нельзя звать во время il2cpp_init → пауза после загрузки движка;
 *   - free() на il2cpp_type_get_name() валит процесс → не освобождаем;
 *   - поток регистрируется в IL2CPP (il2cpp_thread_attach) сразу после резолва:
 *     без этого первая же аллокация в метаданных даёт abort
 *     "Collecting from unknown thread" — падает вся игра;
 *   - опрос из fork-ребёнка невозможен → работаем в потоке игры, но аккуратно.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <link.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* ---------- непрозрачные типы ---------- */
typedef void Il2CppDomain;
typedef void Il2CppAssembly;
typedef void Il2CppImage;
typedef void Il2CppClass;
typedef void Il2CppType;
typedef void FieldInfo;
typedef void Il2CppObject;

#define FIELD_ATTRIBUTE_STATIC 0x0010
#define FIELD_ATTRIBUTE_LITERAL 0x0040

/* ---------- API движка ---------- */
typedef Il2CppDomain *(*fn_domain_get)(void);
typedef const Il2CppAssembly **(*fn_domain_get_assemblies)(const Il2CppDomain *, size_t *);
typedef const Il2CppImage *(*fn_assembly_get_image)(const Il2CppAssembly *);
typedef Il2CppClass *(*fn_image_get_class)(const Il2CppImage *, size_t);
typedef size_t (*fn_image_get_class_count)(const Il2CppImage *);
typedef const char *(*fn_image_get_name)(const Il2CppImage *);
typedef const char *(*fn_class_get_name)(Il2CppClass *);
typedef const char *(*fn_class_get_namespace)(Il2CppClass *);
typedef FieldInfo *(*fn_class_get_fields)(Il2CppClass *, void **);
typedef const char *(*fn_field_get_name)(FieldInfo *);
typedef const Il2CppType *(*fn_field_get_type)(FieldInfo *);
typedef int (*fn_field_get_flags)(FieldInfo *);
typedef size_t (*fn_field_get_offset)(FieldInfo *);
typedef char *(*fn_type_get_name)(const Il2CppType *);
typedef int (*fn_type_get_type)(const Il2CppType *);
typedef void (*fn_field_get_value)(Il2CppObject *, FieldInfo *, void *);
typedef void (*fn_field_static_get_value)(FieldInfo *, void *);
typedef Il2CppClass *(*fn_object_get_class)(Il2CppObject *);
typedef Il2CppClass *(*fn_class_from_type)(const Il2CppType *);
typedef size_t (*fn_array_length)(Il2CppObject *);
typedef uint32_t (*fn_array_object_header_size)(void);
typedef int (*fn_class_array_element_size)(Il2CppClass *);
typedef size_t (*fn_class_instance_size)(Il2CppClass *);
typedef Il2CppClass *(*fn_field_get_parent)(FieldInfo *);
typedef Il2CppClass *(*fn_class_get_parent)(Il2CppClass *);
typedef void *(*fn_thread_attach)(Il2CppDomain *);

static struct api {
    fn_domain_get domain_get;
    fn_domain_get_assemblies domain_get_assemblies;
    fn_assembly_get_image assembly_get_image;
    fn_image_get_class image_get_class;
    fn_image_get_class_count image_get_class_count;
    fn_image_get_name image_get_name;
    fn_class_get_name class_get_name;
    fn_class_get_namespace class_get_namespace;
    fn_class_get_fields class_get_fields;
    fn_field_get_name field_get_name;
    fn_field_get_type field_get_type;
    fn_field_get_flags field_get_flags;
    fn_field_get_offset field_get_offset;
    fn_type_get_name type_get_name;
    fn_type_get_type type_get_type;
    fn_field_get_value field_get_value;
    fn_field_static_get_value field_static_get_value;
    fn_object_get_class object_get_class;
    fn_class_from_type class_from_type;
    fn_array_length array_length;
    fn_array_object_header_size array_object_header_size;
    fn_class_array_element_size class_array_element_size;
    fn_class_instance_size class_instance_size;
    fn_field_get_parent field_get_parent;
    fn_class_get_parent class_get_parent;
    fn_thread_attach thread_attach;
} A;

static char g_outdir[1024];
static FILE *g_log;

static void logf_(const char *fmt, ...) {
    if (!g_log) return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
}

/* ---------- защищённое чтение ---------- */
static sigjmp_buf g_jb;
static volatile sig_atomic_t g_guard = 0;
static volatile int g_faults = 0;
static pthread_t g_self;
static struct sigaction g_old_segv, g_old_bus;
static int g_handlers_installed = 0;

static void fault_handler(int sig, siginfo_t *si, void *ctx) {
    (void)si; (void)ctx;
    if (g_guard && pthread_equal(pthread_self(), g_self)) {
        g_guard = 0;
        g_faults++;
        siglongjmp(g_jb, 1);
    }
    /* это не наш поток — возвращаем прежнее поведение игры */
    struct sigaction *old = (sig == SIGBUS) ? &g_old_bus : &g_old_segv;
    if (old->sa_flags & SA_SIGINFO) {
        if (old->sa_sigaction) old->sa_sigaction(sig, si, ctx);
    } else if (old->sa_handler != SIG_DFL && old->sa_handler != SIG_IGN) {
        old->sa_handler(sig);
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

static void install_guards(void) {
    if (g_handlers_installed) return;
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = fault_handler;
    sa.sa_flags = SA_SIGINFO | SA_NODEFER;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, &g_old_segv);
    sigaction(SIGBUS, &sa, &g_old_bus);
    g_handlers_installed = 1;
}

static void restore_guards(void) {
    if (!g_handlers_installed) return;
    sigaction(SIGSEGV, &g_old_segv, NULL);
    sigaction(SIGBUS, &g_old_bus, NULL);
    g_handlers_installed = 0;
}

/* указатель, которому можно верить хоть немного */
static int ptr_ok(const void *p) {
    uintptr_t v = (uintptr_t)p;
    return p != NULL && (v & 7u) == 0 && v > 0x10000 && v < (uintptr_t)0x7fffffffffffULL;
}

/* макрос: выполнить опасное выражение, при падении вернуть fallback */
#define G_TRY (g_guard = 1, sigsetjmp(g_jb, 1) == 0)

static int32_t mem_i32(const void *base, size_t off) {
    int32_t v = 0;
    if (G_TRY) { v = *(const int32_t *)((const char *)base + off); g_guard = 0; }
    else { g_guard = 0; }
    return v;
}

static uint16_t mem_u16(const void *base, size_t off) {
    uint16_t v = 0;
    if (G_TRY) { v = *(const uint16_t *)((const char *)base + off); g_guard = 0; }
    else { g_guard = 0; }
    return v;
}

static void *mem_ptr(const void *base, size_t off) {
    void *v = NULL;
    if (G_TRY) { v = *(void *const *)((const char *)base + off); g_guard = 0; }
    else { g_guard = 0; }
    return v;
}

/* Обёртки: каждый вызов движка — в собственной функции с setjmp-кадром,
 * чтобы в циклах не было sigsetjmp (иначе longjmp портит счётчики). */
static Il2CppDomain *w_domain(void) {
    Il2CppDomain *r = NULL;
    if (G_TRY) { r = A.domain_get(); g_guard = 0; } else g_guard = 0;
    return r;
}
static const Il2CppAssembly **w_assemblies(Il2CppDomain *d, size_t *n) {
    const Il2CppAssembly **r = NULL;
    if (G_TRY) { r = A.domain_get_assemblies(d, n); g_guard = 0; } else g_guard = 0;
    return r;
}
static Il2CppImage *w_asm_image(const Il2CppAssembly *a) {
    Il2CppImage *r = NULL;
    if (G_TRY) { r = (Il2CppImage *)A.assembly_get_image(a); g_guard = 0; } else g_guard = 0;
    return r;
}
static size_t w_img_count(Il2CppImage *img) {
    size_t r = 0;
    if (G_TRY) { r = A.image_get_class_count(img); g_guard = 0; } else g_guard = 0;
    return r;
}
static Il2CppClass *w_img_class(Il2CppImage *img, size_t i) {
    Il2CppClass *r = NULL;
    if (G_TRY) { r = A.image_get_class(img, i); g_guard = 0; } else g_guard = 0;
    return r;
}
static FieldInfo *w_cls_fields(Il2CppClass *k, void **it) {
    FieldInfo *r = NULL;
    if (G_TRY) { r = A.class_get_fields(k, it); g_guard = 0; } else g_guard = 0;
    return r;
}
static void *w_attach(Il2CppDomain *d) {
    void *r = NULL;
    if (G_TRY) { r = A.thread_attach(d); g_guard = 0; } else g_guard = 0;
    return r;
}

static Il2CppClass *w_obj_class(Il2CppObject *o) {
    Il2CppClass *r = NULL;
    if (G_TRY) { r = A.object_get_class(o); g_guard = 0; } else g_guard = 0;
    return r;
}
static void w_static_ptr(FieldInfo *f, void **out) {
    *out = NULL;
    if (G_TRY) { A.field_static_get_value(f, out); g_guard = 0; } else { g_guard = 0; *out = NULL; }
}
static int w_type_type(const Il2CppType *t) {
    int r = 0;
    if (G_TRY) { r = A.type_get_type(t); g_guard = 0; } else g_guard = 0;
    return r;
}
static size_t w_array_len(Il2CppObject *a) {
    size_t r = 0;
    if (G_TRY) { r = A.array_length(a); g_guard = 0; } else g_guard = 0;
    return r;
}
static size_t w_array_header(void) {
    size_t r = 32;
    if (G_TRY) { r = A.array_object_header_size(); g_guard = 0; } else g_guard = 0;
    return r;
}
static int w_elem_size(Il2CppClass *k) {
    int r = 0;
    if (G_TRY) { r = A.class_array_element_size(k); g_guard = 0; } else g_guard = 0;
    return r;
}

/* ---------- GameAssembly ---------- */
struct find_ctx { char path[4096]; int found; };

static int phdr_cb(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size;
    struct find_ctx *c = (struct find_ctx *)data;
    const char *n = info->dlpi_name;
    if (!n || !*n) return 0;
    const char *base = strrchr(n, '/');
    base = base ? base + 1 : n;
    if (strcmp(base, "GameAssembly.so") == 0) {
        snprintf(c->path, sizeof(c->path), "%s", n);
        c->found = 1;
        return 1;
    }
    return 0;
}

static int gameassembly_loaded(void) {
    struct find_ctx c = {0};
    dl_iterate_phdr(phdr_cb, &c);
    return c.found;
}

static void *open_gameassembly(void) {
    struct find_ctx c = {0};
    dl_iterate_phdr(phdr_cb, &c);
    if (c.found) {
        void *h = dlopen(c.path, RTLD_LAZY | RTLD_NOLOAD);
        if (h) return h;
    }
    return dlopen("GameAssembly.so", RTLD_LAZY | RTLD_NOLOAD);
}

static int resolve_all(void *h) {
    int missing = 0;
    struct { const char *name; void **slot; } tab[] = {
        {"il2cpp_domain_get", (void **)&A.domain_get},
        {"il2cpp_domain_get_assemblies", (void **)&A.domain_get_assemblies},
        {"il2cpp_assembly_get_image", (void **)&A.assembly_get_image},
        {"il2cpp_image_get_class", (void **)&A.image_get_class},
        {"il2cpp_image_get_class_count", (void **)&A.image_get_class_count},
        {"il2cpp_image_get_name", (void **)&A.image_get_name},
        {"il2cpp_class_get_name", (void **)&A.class_get_name},
        {"il2cpp_class_get_namespace", (void **)&A.class_get_namespace},
        {"il2cpp_class_get_fields", (void **)&A.class_get_fields},
        {"il2cpp_field_get_name", (void **)&A.field_get_name},
        {"il2cpp_field_get_type", (void **)&A.field_get_type},
        {"il2cpp_field_get_flags", (void **)&A.field_get_flags},
        {"il2cpp_field_get_offset", (void **)&A.field_get_offset},
        {"il2cpp_type_get_name", (void **)&A.type_get_name},
        {"il2cpp_type_get_type", (void **)&A.type_get_type},
        {"il2cpp_field_get_value", (void **)&A.field_get_value},
        {"il2cpp_field_static_get_value", (void **)&A.field_static_get_value},
        {"il2cpp_object_get_class", (void **)&A.object_get_class},
        {"il2cpp_class_from_type", (void **)&A.class_from_type},
        {"il2cpp_array_length", (void **)&A.array_length},
        {"il2cpp_array_object_header_size", (void **)&A.array_object_header_size},
        {"il2cpp_class_array_element_size", (void **)&A.class_array_element_size},
        {"il2cpp_class_instance_size", (void **)&A.class_instance_size},
        {"il2cpp_field_get_parent", (void **)&A.field_get_parent},
        {"il2cpp_class_get_parent", (void **)&A.class_get_parent},
        {"il2cpp_thread_attach", (void **)&A.thread_attach},
    };
    for (size_t i = 0; i < sizeof(tab) / sizeof(tab[0]); ++i) {
        *tab[i].slot = dlsym(h, tab[i].name);
        if (!*tab[i].slot) *tab[i].slot = dlsym(RTLD_DEFAULT, tab[i].name);
        if (!*tab[i].slot) { missing++; logf_("НЕТ СИМВОЛА %s", tab[i].name); }
    }
    return missing;
}

/* ---------- работа с классами и полями ---------- */
static Il2CppImage *g_images[512];
static size_t g_nimg = 0;
static Il2CppClass *g_gamestate_cls, *g_playstate_cls;
/* разведка 0.5.x: карта (юниты/города) и события (ActionStack) */
static Il2CppClass *g_mapdata_cls, *g_tiledata_cls, *g_unitstate_cls;
static Il2CppClass *g_impstate_cls, *g_actionbase_cls;

static int class_is(Il2CppObject *o, Il2CppClass *k) {
    if (!ptr_ok(o) || !k) return 0;
    return w_obj_class(o) == k;
}

static Il2CppClass *w_parent(Il2CppClass *k) {
    Il2CppClass *r = NULL;
    if (k && A.class_get_parent) {
        if (G_TRY) { r = A.class_get_parent(k); g_guard = 0; } else g_guard = 0;
    }
    return r;
}

/* поле ищется по цепочке наследования: currentGameState объявлен в ClientBase,
 * а объект — ReplayClient/LocalClient, у которых своих полей нет */
static FieldInfo *cls_field(Il2CppClass *k, const char *name) {
    for (int depth = 0; k && depth < 12; ++depth) {
        void *it = NULL;
        for (;;) {
            FieldInfo *f = w_cls_fields(k, &it);
            if (!f) break;
            const char *n = A.field_get_name(f);
            if (n && !strcmp(n, name)) return f;
        }
        k = w_parent(k);
    }
    return NULL;
}

/* чтение поля-значения по имени; 0 — не прочиталось */
static int f_i32(void *obj, Il2CppClass *k, const char *name, int32_t *out) {
    FieldInfo *f = cls_field(k, name);
    if (!f || !obj) return 0;
    int32_t v = 0;
    if (G_TRY) { A.field_get_value(obj, f, &v); g_guard = 0; }
    else { g_guard = 0; return 0; }
    *out = v;
    return 1;
}

static int f_u32(void *obj, Il2CppClass *k, const char *name, uint32_t *out) {
    FieldInfo *f = cls_field(k, name);
    if (!f || !obj) return 0;
    uint32_t v = 0;
    if (G_TRY) { A.field_get_value(obj, f, &v); g_guard = 0; }
    else { g_guard = 0; return 0; }
    *out = v;
    return 1;
}

static int f_u8(void *obj, Il2CppClass *k, const char *name, uint8_t *out) {
    FieldInfo *f = cls_field(k, name);
    if (!f || !obj) return 0;
    uint8_t v = 0;
    if (G_TRY) { A.field_get_value(obj, f, &v); g_guard = 0; }
    else { g_guard = 0; return 0; }
    *out = v;
    return 1;
}

static int f_u16(void *obj, Il2CppClass *k, const char *name, uint16_t *out) {
    FieldInfo *f = cls_field(k, name);
    if (!f || !obj) return 0;
    uint16_t v = 0;
    if (G_TRY) { A.field_get_value(obj, f, &v); g_guard = 0; }
    else { g_guard = 0; return 0; }
    *out = v;
    return 1;
}

static void *f_ptr(void *obj, Il2CppClass *k, const char *name) {
    FieldInfo *f = cls_field(k, name);
    if (!f || !obj) return NULL;
    void *v = NULL;
    if (G_TRY) { A.field_get_value(obj, f, &v); g_guard = 0; }
    else { g_guard = 0; return NULL; }
    return v;
}

/* строка .NET → utf8 (упрощённо: latin/utf16 → ascii-совместимо) */
static void read_str(void *s, char *buf, size_t cap) {
    buf[0] = 0;
    if (!ptr_ok(s) || cap < 2) return;
    int32_t len = mem_i32(s, 16); /* Il2CppString.length */
    if (len <= 0 || len > 128) return;
    size_t o = 0;
    for (int32_t i = 0; i < len && o + 1 < cap; ++i) {
        uint16_t ch = mem_u16(s, 20 + (size_t)i * 2);
        buf[o++] = (ch < 128) ? (char)ch : '?';
    }
    buf[o] = 0;
}

/* элементы List<T> */
static void *list_items(void *list) {
    if (!ptr_ok(list)) return NULL;
    void *arr = NULL;
    if (G_TRY) { arr = *(void **)((char *)list + 16); g_guard = 0; }
    else { g_guard = 0; return NULL; }
    return arr;
}

static int32_t list_size(void *list) {
    if (!ptr_ok(list)) return 0;
    return mem_i32(list, 24);
}

/* элемент массива ссылочного типа */
static void *arr_at(void *arr, int i) {
    if (!ptr_ok(arr)) return NULL;
    Il2CppClass *kc = w_obj_class(arr);
    if (!kc) return NULL;
    size_t hdr = w_array_header();
    if (hdr < 16 || hdr > 128) hdr = 32;
    int esz = w_elem_size(kc);
    if (esz <= 0 || esz > 64) esz = 8;
    return mem_ptr(arr, hdr + (size_t)i * (size_t)esz);
}

/* ---------- поиск живого GameState ---------- */
static Il2CppClass *class_by_name_in_image(Il2CppImage *img, const char *name) {
    size_t n = w_img_count(img);
    if (n > 100000) return NULL;
    for (size_t i = 0; i < n; ++i) {
        Il2CppClass *k = w_img_class(img, i);
        if (!k) continue;
        const char *cn = A.class_get_name(k);
        if (cn && !strcmp(cn, name)) return k;
    }
    return NULL;
}

static Il2CppClass *find_class_all(const char *name) {
    for (size_t i = 0; i < g_nimg; ++i) {
        Il2CppClass *k = class_by_name_in_image(g_images[i], name);
        if (k) return k;
    }
    return NULL;
}

/* как find_class_all, но сначала игровые сборки: имена вроде TileData
 * есть и в сборках Unity (UnityEngine.Tilemaps, URP) — они не подходят */
static Il2CppClass *find_class_pref(const char *name) {
    static const char *pref[] = {"GameLogicAssembly.dll", "PolytopiaAssembly.dll"};
    for (size_t p = 0; p < 2; ++p)
        for (size_t i = 0; i < g_nimg; ++i) {
            const char *iname = A.image_get_name(g_images[i]);
            if (!iname || strcmp(iname, pref[p])) continue;
            Il2CppClass *k = class_by_name_in_image(g_images[i], name);
            if (k) return k;
        }
    return find_class_all(name);
}

/* Статические поля, тип которых содержит «GameState»: собираем один раз,
 * потом только читаем их (иначе каждые 2 с сканируем 17 тыс. классов). */
static FieldInfo *g_gs_fields[32];
static int g_gs_field_n = 0;
static int g_gs_scanned = 0;

static void collect_gs_static_fields(void) {
    if (g_gs_scanned) return;
    g_gs_scanned = 1;
    for (size_t i = 0; i < g_nimg; ++i) {
        size_t n = w_img_count(g_images[i]);
        if (n > 100000) continue;
        for (size_t c = 0; c < n; ++c) {
            Il2CppClass *k = w_img_class(g_images[i], c);
            if (!k) continue;
            void *it = NULL;
            for (;;) {
                FieldInfo *f = w_cls_fields(k, &it);
                if (!f) break;
                int fl = A.field_get_flags(f);
                if (!(fl & FIELD_ATTRIBUTE_STATIC)) continue;
                const Il2CppType *t = A.field_get_type(f);
                if (!t) continue;
                char *tn = A.type_get_name(t); /* НЕ free() */
                int is_gs = tn && strstr(tn, "GameState") != NULL;
                if (!is_gs) continue;
                if (g_gs_field_n < 32) g_gs_fields[g_gs_field_n++] = f;
            }
        }
    }
    logf_("статических полей с типом GameState: %d", g_gs_field_n);
}

static void *gamestate_from_statics(char *where, size_t cap) {
    collect_gs_static_fields();
    for (int i = 0; i < g_gs_field_n; ++i) {
        void *v = NULL;
        w_static_ptr(g_gs_fields[i], &v);
        if (ptr_ok(v)) {
            FieldInfo *f = g_gs_fields[i];
            Il2CppClass *k = A.field_get_parent ? A.field_get_parent(f) : NULL;
            snprintf(where, cap, "static %s.%s", k && A.class_get_name(k) ? A.class_get_name(k) : "?",
                     A.field_get_name(f));
            return v;
        }
    }
    return NULL;
}

/* признак массива/списка и разворачивание элементов в очередь */
static int is_array_class(Il2CppClass *k) {
    const char *n = A.class_get_name(k);
    if (!n) return 0;
    size_t len = strlen(n);
    return len > 2 && !strcmp(n + len - 2, "[]");
}

static int is_list_class(Il2CppClass *k) {
    const char *n = A.class_get_name(k);
    return n && (!strncmp(n, "List`1", 6) || !strncmp(n, "Dictionary`2", 12));
}

/* обход ссылок от GameManager.instance в поисках GameState */
static void *gamestate_from_crawl(void *root, Il2CppClass *gs_cls, char *where, size_t cap) {
    if (!ptr_ok(root) || !gs_cls) return NULL;
    enum { MAXN = 4000 };
    static void *seen[MAXN];
    static int seen_n;
    seen_n = 0;
    void *queue[2][MAXN];
    static char qpath[2][MAXN][128]; /* путь на каждый элемент каждой очереди */
    int qn[2] = {0, 0};
    queue[0][0] = root;
    snprintf(qpath[0][0], sizeof(qpath[0][0]), "GameManager.instance");
    seen[seen_n++] = root;
    qn[0] = 1;
    int cur = 0;
    for (int depth = 0; depth < 4; ++depth) {
        int next = 1 - cur;
        qn[next] = 0;
        for (int qi = 0; qi < qn[cur]; ++qi) {
            void *obj = queue[cur][qi];
            if (!ptr_ok(obj)) continue;
            if (class_is(obj, gs_cls)) {
                snprintf(where, cap, "%s", qpath[cur][qi]);
                return obj;
            }
            Il2CppClass *kc = w_obj_class(obj);
            if (!kc) continue;
            if (getenv("POLYREAD_DUMP_TREE") && depth <= 1)
                logf_("дерево: %s → %s", qpath[cur][qi],
                      A.class_get_name(kc) ? A.class_get_name(kc) : "?");

            /* массив: кладём элементы в очередь, иначе состояние внутри
             * List<PlayerState>/массивов не находится */
            if (is_array_class(kc)) {
                size_t n = w_array_len(obj);
                if (n > 256) n = 256;
                for (size_t ai = 0; ai < n; ++ai) {
                    void *el = arr_at(obj, (int)ai);
                    if (!ptr_ok(el)) continue;
                    int dup = 0;
                    for (int s = 0; s < seen_n; ++s) if (seen[s] == el) { dup = 1; break; }
                    if (dup || seen_n >= MAXN || qn[next] >= MAXN) continue;
                    seen[seen_n++] = el;
                    queue[next][qn[next]] = el;
                    snprintf(qpath[next][qn[next]], sizeof(qpath[0][0]), "%s[%zu]", qpath[cur][qi], ai);
                    qn[next]++;
                }
                continue;
            }
            /* List<T>: идём внутрь _items, дальше сработает ветка массива */
            if (is_list_class(kc)) {
                void *items = list_items(obj);
                if (ptr_ok(items)) {
                    int dup = 0;
                    for (int s = 0; s < seen_n; ++s) if (seen[s] == items) { dup = 1; break; }
                    if (!dup && seen_n < MAXN && qn[next] < MAXN) {
                        seen[seen_n++] = items;
                        queue[next][qn[next]] = items;
                        snprintf(qpath[next][qn[next]], sizeof(qpath[0][0]), "%s._items", qpath[cur][qi]);
                        qn[next]++;
                    }
                }
            }
            void *it = NULL;
            for (;;) {
                FieldInfo *f = w_cls_fields(kc, &it);
                if (!f) break;
                int fl = A.field_get_flags(f);
                if (fl & FIELD_ATTRIBUTE_STATIC) continue;
                const Il2CppType *t = A.field_get_type(f);
                if (!t) continue;
                int tt = w_type_type(t);
                /* IL2CPP_TYPE_CLASS=0x12, IL2CPP_TYPE_OBJECT=0x1C, ARRAY=0x14, GENERICINST=0x15 */
                if (tt != 0x12 && tt != 0x1c && tt != 0x14 && tt != 0x15) continue;
                size_t off = A.field_get_offset(f);
                void *child = mem_ptr(obj, off);
                if (!ptr_ok(child)) continue;
                int dup = 0;
                for (int s = 0; s < seen_n; ++s) if (seen[s] == child) { dup = 1; break; }
                if (dup || seen_n >= MAXN || qn[next] >= MAXN) continue;
                seen[seen_n++] = child;
                queue[next][qn[next]] = child;
                char parent[96];
                snprintf(parent, sizeof(parent), "%s", qpath[cur][qi]);
                snprintf(qpath[next][qn[next]], sizeof(qpath[0][0]), "%s.%s", parent,
                         A.field_get_name(f));
                qn[next]++;
            }
        }
        cur = next;
        if (!qn[cur]) break;
    }
    return NULL;
}

/* ---------- диагностический смотр (POLYREAD_DUMP_SWEEP=1) ---------- */
static int interesting(const char *n) {
    if (!n) return 0;
    return strstr(n, "Game") || strstr(n, "Replay") || strstr(n, "Session") ||
           strstr(n, "Logic") || strstr(n, "Match");
}

static void sweep(void) {
    logf_("=== СМОТР: статические поля с типом GameState ===");
    collect_gs_static_fields();
    for (int i = 0; i < g_gs_field_n; ++i) {
        FieldInfo *f = g_gs_fields[i];
        void *v = NULL;
        w_static_ptr(f, &v);
        Il2CppClass *owner = A.field_get_parent ? A.field_get_parent(f) : NULL;
        logf_("  %s.%s = %p", owner && A.class_get_name(owner) ? A.class_get_name(owner) : "?",
              A.field_get_name(f), v);
    }

    logf_("=== СМОТР: статические ссылки игровых сборок ===");
    for (size_t i = 0; i < g_nimg; ++i) {
        const char *in = A.image_get_name(g_images[i]);
        if (!in) continue;
        if (strcmp(in, "PolytopiaAssembly.dll") && strcmp(in, "GameLogicAssembly.dll")) continue;
        size_t n = w_img_count(g_images[i]);
        if (n > 100000) continue;
        for (size_t c = 0; c < n; ++c) {
            Il2CppClass *k = w_img_class(g_images[i], c);
            if (!k) continue;
            void *it = NULL;
            for (;;) {
                FieldInfo *f = w_cls_fields(k, &it);
                if (!f) break;
                int fl = A.field_get_flags(f);
                if (!(fl & FIELD_ATTRIBUTE_STATIC) || (fl & FIELD_ATTRIBUTE_LITERAL)) continue;
                const Il2CppType *t = A.field_get_type(f);
                if (!t) continue;
                int tt = w_type_type(t);
                if (tt != 0x12 && tt != 0x1c && tt != 0x14 && tt != 0x15) continue;
                void *v = NULL;
                w_static_ptr(f, &v);
                if (!ptr_ok(v)) continue;
                Il2CppClass *vc = w_obj_class(v);
                const char *vn = vc ? A.class_get_name(vc) : NULL;
                if (!interesting(vn)) continue;
                logf_("  стат %s.%s → %p (%s)", A.class_get_name(k) ? A.class_get_name(k) : "?",
                      A.field_get_name(f), v, vn ? vn : "?");
            }
        }
    }

    logf_("=== СМОТР: дерево от GameManager.instance (интересные классы) ===");
    Il2CppClass *gm_cls = find_class_all("GameManager");
    if (gm_cls) {
        FieldInfo *inst = cls_field(gm_cls, "instance");
        void *gm = NULL;
        if (inst) w_static_ptr(inst, &gm);
        logf_("  instance=%p", gm);
        /* двухуровневый обход с журналом */
        if (ptr_ok(gm)) {
            Il2CppClass *kc = w_obj_class(gm);
            void *it = NULL;
            for (;;) {
                FieldInfo *f = w_cls_fields(kc, &it);
                if (!f) break;
                if (A.field_get_flags(f) & FIELD_ATTRIBUTE_STATIC) continue;
                const Il2CppType *t = A.field_get_type(f);
                if (!t) continue;
                int tt = w_type_type(t);
                if (tt != 0x12 && tt != 0x1c && tt != 0x14 && tt != 0x15) continue;
                void *child = mem_ptr(gm, A.field_get_offset(f));
                if (!ptr_ok(child)) continue;
                Il2CppClass *cc = w_obj_class(child);
                const char *cn = cc ? A.class_get_name(cc) : NULL;
                if (!interesting(cn)) continue;
                logf_("  instance.%s → %p (%s)", A.field_get_name(f), child, cn ? cn : "?");
            }
        }
    }
    /* структура менеджеров GameManager: два уровня с полным логом */
    const char *roots[] = {"client", "fakeSession", "replaysManager",
                           "syncedLocalGameDataManager", "lobbyManager", "remoteGameDataManager"};
    for (size_t ri = 0; ri < sizeof(roots) / sizeof(roots[0]); ++ri) {
        logf_("=== СМОТР: GameManager.%s ===", roots[ri]);
        if (!gm_cls) continue;
        FieldInfo *inst = cls_field(gm_cls, "instance");
        void *gm = NULL;
        if (inst) w_static_ptr(inst, &gm);
        void *rm = gm ? f_ptr(gm, gm_cls, roots[ri]) : NULL;
        if (!ptr_ok(rm)) { logf_("  %s = %p", roots[ri], rm); continue; }
        logf_("  %s=%p (%s)", roots[ri], rm, A.class_get_name(w_obj_class(rm)) ? A.class_get_name(w_obj_class(rm)) : "?");
        {
            Il2CppClass *rk = w_obj_class(rm);
            void *it = NULL;
            for (;;) {
                FieldInfo *f = w_cls_fields(rk, &it);
                if (!f) break;
                if (A.field_get_flags(f) & FIELD_ATTRIBUTE_STATIC) continue;
                const Il2CppType *t = A.field_get_type(f);
                if (!t) continue;
                int tt = w_type_type(t);
                const char *tn = A.type_get_name(t);
                size_t off = A.field_get_offset(f);
                void *child = NULL;
                if (tt == 0x12 || tt == 0x1c || tt == 0x14 || tt == 0x15) child = mem_ptr(rm, off);
                Il2CppClass *cc = child ? w_obj_class(child) : NULL;
                logf_("    replaysManager.%-28s %-38s %p %s", A.field_get_name(f),
                      tn ? tn : "?", child, cc && A.class_get_name(cc) ? A.class_get_name(cc) : "");
                /* второй уровень для ссылочных полей */
                if (ptr_ok(child) && cc) {
                    void *it2 = NULL;
                    for (int lim = 0; lim < 12; ++lim) {
                        FieldInfo *f2 = w_cls_fields(cc, &it2);
                        if (!f2) break;
                        if (A.field_get_flags(f2) & FIELD_ATTRIBUTE_STATIC) continue;
                        const Il2CppType *t2 = A.field_get_type(f2);
                        if (!t2) continue;
                        int tt2 = w_type_type(t2);
                        if (tt2 != 0x12 && tt2 != 0x1c && tt2 != 0x14 && tt2 != 0x15) continue;
                        void *ch2 = mem_ptr(child, A.field_get_offset(f2));
                        Il2CppClass *c2 = ch2 ? w_obj_class(ch2) : NULL;
                        logf_("      .%-26s → %p (%s)", A.field_get_name(f2), ch2,
                              c2 && A.class_get_name(c2) ? A.class_get_name(c2) : "-");
                    }
                }
            }
        }
    }
    logf_("=== СМОТР завершён ===");
}

static int snapshot(void *gs, int *turn_out);
static int build_snapshot(void *gs, int turn);
static void write_snapshot(int turn, int force);
static void collect_events(void *gs);
static void events_reset(void);

/* печать полей указанных классов: POLYREAD_DUMP_FIELDS="ReplayInterface,Timeline" */
static void dump_fields_mode(const char *list) {
    char buf[512];
    snprintf(buf, sizeof(buf), "%s", list);
    char *save = NULL;
    for (char *name = strtok_r(buf, ",", &save); name; name = strtok_r(NULL, ",", &save)) {
        while (*name == ' ') name++;
        Il2CppClass *k = find_class_all(name);
        logf_("=== ПОЛЯ %s: %p ===", name, (void *)k);
        if (!k) continue;
        void *it = NULL;
        for (;;) {
            FieldInfo *f = w_cls_fields(k, &it);
            if (!f) break;
            int fl = A.field_get_flags(f);
            const Il2CppType *t = A.field_get_type(f);
            char *tn = t ? A.type_get_name(t) : NULL;
            size_t off = A.field_get_offset(f);
            void *sv = NULL;
            if (fl & FIELD_ATTRIBUTE_STATIC) w_static_ptr(f, &sv);
            logf_("  %-32s %-46s off=%-4zu%s%s", A.field_get_name(f), tn ? tn : "?", off,
                  (fl & FIELD_ATTRIBUTE_STATIC) ? "  [static]" : "",
                  (fl & FIELD_ATTRIBUTE_STATIC) && ptr_ok(sv) ? " = (объект)" : "");
        }
    }
}

/* значения статических кандидатов (диагностика) */
static void log_gs_statics(void) {
    collect_gs_static_fields();
    for (int i = 0; i < g_gs_field_n; ++i) {
        void *v = NULL;
        w_static_ptr(g_gs_fields[i], &v);
        if (!ptr_ok(v)) continue;
        FieldInfo *f = g_gs_fields[i];
        Il2CppClass *owner = A.field_get_parent ? A.field_get_parent(f) : NULL;
        logf_("  стат %s.%s = %p", owner && A.class_get_name(owner) ? A.class_get_name(owner) : "?",
              A.field_get_name(f), v);
    }
}

/* ---------- наблюдение по тактам (POLYREAD_DUMP_WATCH=1) ---------- */
static void watch_tacts(void) {
    Il2CppClass *gm_cls = find_class_all("GameManager");
    FieldInfo *inst = gm_cls ? cls_field(gm_cls, "instance") : NULL;
    const char *probe_fields[] = {"client", "fakeSession", "replaysManager", "lobbyManager"};
    for (int tact = 0; tact < 12; ++tact) {
        void *gm = NULL;
        if (inst) w_static_ptr(inst, &gm);
        logf_("--- такт %d: instance=%p ---", tact, gm);
        if (ptr_ok(gm) && gm_cls) {
            for (size_t i = 0; i < sizeof(probe_fields) / sizeof(probe_fields[0]); ++i) {
                void *v = f_ptr(gm, gm_cls, probe_fields[i]);
                Il2CppClass *vc = v ? w_obj_class(v) : NULL;
                logf_("    %s = %p (%s)", probe_fields[i], v,
                      vc && A.class_get_name(vc) ? A.class_get_name(vc) : "-");
                if (!ptr_ok(v) || !vc) continue;
                /* первый уровень полей клиента/сессии */
                void *it = NULL;
                int shown = 0;
                for (;;) {
                    FieldInfo *f = w_cls_fields(vc, &it);
                    if (!f || shown > 14) break;
                    if (A.field_get_flags(f) & FIELD_ATTRIBUTE_STATIC) continue;
                    const Il2CppType *t = A.field_get_type(f);
                    if (!t) continue;
                    int tt = w_type_type(t);
                    if (tt != 0x12 && tt != 0x1c && tt != 0x14 && tt != 0x15) continue;
                    void *ch = mem_ptr(v, A.field_get_offset(f));
                    if (!ptr_ok(ch)) continue;
                    Il2CppClass *cc = w_obj_class(ch);
                    logf_("        .%s → %s", A.field_get_name(f),
                          cc && A.class_get_name(cc) ? A.class_get_name(cc) : "?");
                    shown++;
                }
            }
            char where[160] = {0};
            void *gs = gamestate_from_statics(where, sizeof(where));
            if (!gs) gs = gamestate_from_crawl(gm, g_gamestate_cls, where, sizeof(where));
            if (gs) {
                int turn = 0;
                int ok = snapshot(gs, &turn);
                logf_("    GameState НАЙДЕН: %s, ход=%d, снимок=%d", where, turn, ok);
                if (ok) {
                    collect_events(gs);
                    if (build_snapshot(gs, turn)) { write_snapshot(turn, 1); events_reset(); }
                }
            } else {
                logf_("    GameState не найден");
            }
        }
        fflush(g_log);
        sleep(8);
    }
}

/* ---------- фильтр «только реплей» ----------
 * В живом матче читать состояние нельзя: это нечестная игра. Признак реплея —
 * класс клиента: ReplayClient против LocalClient/RemoteClient/HotseatClient.
 * Если признак не подтверждён, снимки не пишутся (fail-closed). */
static char g_client_class[96];
static int g_replay_only = 1;
static int g_live_noted = 0;

static int replay_mode_ok(void) {
    if (!g_replay_only) return 1;
    const char *need = getenv("POLYREAD_DUMP_REQUIRE");
    if (!need || !*need) need = "ReplayClient";
    if (!g_client_class[0]) return 0;           /* клиент ещё не найден */
    return strstr(g_client_class, need) != NULL;
}

/* живое состояние из клиента: ClientBase.currentGameState */
static void *g_client = NULL;
static Il2CppClass *g_client_cls = NULL;

static void *state_from_client(Il2CppClass *gm_cls, char *where, size_t cap) {
    if (!gm_cls) return NULL;
    FieldInfo *inst = cls_field(gm_cls, "instance");
    void *gm = NULL;
    if (inst) w_static_ptr(inst, &gm);
    if (!ptr_ok(gm)) return NULL;
    const char *holders[] = {"client", "fakeSession"};
    for (size_t i = 0; i < 2; ++i) {
        void *h = f_ptr(gm, gm_cls, holders[i]);
        logf_("  %s = %p", holders[i], h);
        if (!ptr_ok(h)) continue;
        Il2CppClass *hc = w_obj_class(h);
        if (!hc) continue;
        logf_("  %s класс = %s", holders[i], A.class_get_name(hc) ? A.class_get_name(hc) : "?");
        const char *fields[] = {"currentGameState", "targetGameState", "initialGameState"};
        for (size_t k = 0; k < 3; ++k) {
            void *st = f_ptr(h, hc, fields[k]);
            logf_("    %s.%s = %p", holders[i], fields[k], st);
            if (ptr_ok(st) && class_is(st, g_gamestate_cls)) {
                const char *cn = A.class_get_name(hc);
                snprintf(g_client_class, sizeof(g_client_class), "%s", cn ? cn : "");
                snprintf(where, cap, "GameManager.%s.%s", holders[i], fields[k]);
                g_client = h;
                g_client_cls = hc;
                return st;
            }
        }
    }
    return NULL;
}

/* ---------- снимок ---------- */
static int g_last_turn = -1;
static unsigned long g_frames = 0, g_written = 0;
static char g_turn_json[65536];

/* Лёгкий опрос: только номер хода (тяжёлая часть — в build_snapshot,
 * он вызывается лишь в момент смены хода). */
static int snapshot(void *gs, int *turn_out) {
    int32_t turn = 0;
    if (!f_u32(gs, g_gamestate_cls, "<CurrentTurn>k__BackingField", (uint32_t *)&turn)) return 0;
    *turn_out = turn;
    return 1;
}

/* Карта: армия = юниты на тайлах; производство = сумма production городов
 * (ImprovementState на тайле). Индексируется по owner (Byte, 0..15; 255 — варвары).
 * Дополнительно собирает список городов/улучшений для диффа между ходами
 * (захваты = смена владельца, постройка = новая запись). */
typedef struct { int32_t x; int32_t y; } wc2;

static int f_wc(void *obj, Il2CppClass *k, const char *name, wc2 *out) {
    FieldInfo *f = cls_field(k, name);
    if (!f || !obj) return 0;
    wc2 v = {0, 0};
    if (G_TRY) { A.field_get_value(obj, f, &v); g_guard = 0; }
    else { g_guard = 0; return 0; }
    *out = v;
    return 1;
}

static char g_cities_json[8192];
static size_t g_cities_len = 0;
static int g_cities_n = 0, g_imp_found = 0;
static char g_units_json[16384];
static size_t g_units_len = 0;
static int g_units_n = 0;

static void scan_map(void *gs, int *army, int *production) {
    memset(army, 0, 256 * sizeof(int));
    memset(production, 0, 256 * sizeof(int));
    g_cities_len = 0; g_cities_n = 0; g_imp_found = 0; g_cities_json[0] = 0;
    g_units_len = 0; g_units_n = 0; g_units_json[0] = 0;
    if (!g_mapdata_cls || !g_tiledata_cls) return;
    void *map = f_ptr(gs, g_gamestate_cls, "<Map>k__BackingField");
    if (!ptr_ok(map)) return;
    void *tiles = f_ptr(map, g_mapdata_cls, "tiles");
    if (!ptr_ok(tiles)) return;
    size_t n = w_array_len(tiles);
    if (n == 0 || n > 4096) return;
    for (size_t i = 0; i < n; ++i) {
        void *tile = arr_at(tiles, (int)i);
        if (!ptr_ok(tile)) continue;
        uint8_t tile_owner = 0xFF;
        f_u8(tile, g_tiledata_cls, "owner", &tile_owner);
        if (g_unitstate_cls) {
            void *unit = f_ptr(tile, g_tiledata_cls, "<unit>k__BackingField");
            if (ptr_ok(unit)) {
                uint8_t own = 0xFF;
                if (f_u8(unit, g_unitstate_cls, "owner", &own) && own < 255) army[own]++;
                /* список юнитов: [id,owner,health,type] — для дельт урона */
                if (g_units_n < 256 && g_units_len + 48 < sizeof(g_units_json)) {
                    uint32_t uid = 0; uint16_t hp = 0; int32_t utype = -1;
                    f_u32(unit, g_unitstate_cls, "id", &uid);
                    f_u16(unit, g_unitstate_cls, "health", &hp);
                    f_i32(unit, g_unitstate_cls, "type", &utype);
                    g_units_len += (size_t)snprintf(g_units_json + g_units_len,
                                                    sizeof(g_units_json) - g_units_len,
                                                    "%s[%u,%u,%u,%d]",
                                                    g_units_n ? "," : "", uid,
                                                    (unsigned)own, (unsigned)hp, (int)utype);
                    g_units_n++;
                }
            }
        }
        if (g_impstate_cls) {
            void *imp = f_ptr(tile, g_tiledata_cls, "improvement");
            if (!ptr_ok(imp)) continue;
            g_imp_found++;
            uint8_t own = 0xFF;
            uint16_t prod = 0, level = 0;
            int32_t type = -1;
            f_u8(imp, g_impstate_cls, "owner", &own);
            f_u16(imp, g_impstate_cls, "production", &prod);
            f_u16(imp, g_impstate_cls, "level", &level);
            f_i32(imp, g_impstate_cls, "type", &type);
            wc2 c = {0, 0};
            f_wc(tile, g_tiledata_cls, "coordinates", &c);
            /* владелец города = владелец тайла (ImprovementState.owner не надёжен) */
            uint8_t eff = tile_owner < 255 ? tile_owner : own;
            if (eff < 255 && prod) production[eff] += prod;
            /* список улучшений: [x,y,tile_owner,imp_owner,type,level,production] */
            if (g_cities_n < 96 && g_cities_len + 64 < sizeof(g_cities_json)) {
                g_cities_len += (size_t)snprintf(g_cities_json + g_cities_len,
                                                 sizeof(g_cities_json) - g_cities_len,
                                                 "%s[%d,%d,%u,%u,%d,%u,%u]",
                                                 g_cities_n ? "," : "", c.x, c.y,
                                                 (unsigned)tile_owner, (unsigned)own, type,
                                                 (unsigned)level, (unsigned)prod);
                g_cities_n++;
            }
        }
    }
}

/* События из ActionStack: курсор помнит, сколько уже снято; на снимок хода
 * выгружается дельта. Маркер turn_start (StartTurnAction) позволяет импортёру
 * однозначно отнести события к ходу. Вложенные subActions обходятся рекурсивно. */
static int32_t g_action_cursor = 0;
static void *g_events_gs = NULL;
static char g_events[32768];
static size_t g_events_len = 0;
static int g_events_n = 0, g_events_trunc = 0;
static int g_commands_done = 0;

static void ev(const char *fmt, ...) {
    if (g_events_trunc) return;
    if (g_events_len + 320 >= sizeof(g_events)) { g_events_trunc = 1; return; }
    if (g_events_n) g_events[g_events_len++] = ',';
    va_list ap;
    va_start(ap, fmt);
    int w = vsnprintf(g_events + g_events_len, sizeof(g_events) - g_events_len, fmt, ap);
    va_end(ap);
    if (w < 0 || (size_t)w >= sizeof(g_events) - g_events_len) { g_events_trunc = 1; return; }
    g_events_len += (size_t)w;
    g_events_n++;
}

static void emit_action(void *act, int depth) {
    if (!ptr_ok(act) || depth > 3 || g_events_trunc) return;
    Il2CppClass *k = w_obj_class(act);
    const char *cn = (k && A.class_get_name(k)) ? A.class_get_name(k) : NULL;
    if (!cn) return;

    if (!strcmp(cn, "StartTurnAction")) {
        ev("{\"t\":\"turn_start\"}");
    } else if (!g_actionbase_cls) {
        return;
    } else {
        uint8_t pid = 0;
        f_u8(act, g_actionbase_cls, "<PlayerId>k__BackingField", &pid);
        int32_t v1 = 0, v2 = 0;
        uint8_t old = 0;
        if (!strcmp(cn, "CaptureCityAction")) {
            f_u8(act, k, "<OldOwnerId>k__BackingField", &old);
            f_i32(act, k, "<Score>k__BackingField", &v1);
            ev("{\"t\":\"capture\",\"p\":%u,\"old\":%u,\"score\":%d}",
               (unsigned)pid, (unsigned)old, v1);
        } else if (!strcmp(cn, "AttackAction")) {
            f_i32(act, k, "<Damage>k__BackingField", &v1);
            ev("{\"t\":\"attack\",\"p\":%u,\"dmg\":%d}", (unsigned)pid, v1);
        } else if (!strcmp(cn, "IncreaseCurrencyAction")) {
            f_i32(act, k, "<Amount>k__BackingField", &v1);
            ev("{\"t\":\"income\",\"p\":%u,\"amt\":%d}", (unsigned)pid, v1);
        } else if (!strcmp(cn, "TrainAction")) {
            f_i32(act, k, "<Cost>k__BackingField", &v1);
            f_i32(act, k, "<Type>k__BackingField", &v2);
            ev("{\"t\":\"train\",\"p\":%u,\"cost\":%d,\"utype\":%d}",
               (unsigned)pid, v1, v2);
        } else if (!strcmp(cn, "ResearchAction")) {
            f_i32(act, k, "<Cost>k__BackingField", &v1);
            f_i32(act, k, "<Type>k__BackingField", &v2);
            ev("{\"t\":\"research\",\"p\":%u,\"cost\":%d,\"ttype\":%d}",
               (unsigned)pid, v1, v2);
        } else if (!strcmp(cn, "UpgradeAction")) {
            f_i32(act, k, "<Cost>k__BackingField", &v1);
            ev("{\"t\":\"upgrade\",\"p\":%u,\"cost\":%d}", (unsigned)pid, v1);
        } else if (!strcmp(cn, "KillUnitAction")) {
            ev("{\"t\":\"kill\",\"p\":%u}", (unsigned)pid);
        }
    }

    void *subs = g_actionbase_cls ? f_ptr(act, g_actionbase_cls, "subActions") : NULL;
    int32_t n = list_size(subs);
    void *arr = list_items(subs);
    if (ptr_ok(arr) && n > 0 && n <= 256)
        for (int32_t i = 0; i < n; ++i) emit_action(arr_at(arr, i), depth + 1);
}

/* Добавить новые события из ActionStack в накопитель (вызывается на КАЖДОМ
 * опросе: стек — живая очередь анимаций, действия из него изымаются, поэтому
 * снимать дельту нужно часто, а не только в момент смены хода). */
static void collect_events(void *gs) {
    if (gs != g_events_gs) { g_action_cursor = 0; g_events_gs = gs; g_commands_done = 0; }
    void *stack = f_ptr(gs, g_gamestate_cls, "<ActionStack>k__BackingField");
    int32_t size = list_size(stack);
    if (size <= 0) return;
    if (size < g_action_cursor) g_action_cursor = 0; /* очередь сбросилась */
    void *arr = list_items(stack);
    if (!ptr_ok(arr)) { g_action_cursor = size; return; }
    int budget = 4096;
    for (int32_t i = g_action_cursor; i < size && budget-- > 0; ++i)
        emit_action(arr_at(arr, i), 0);
    g_action_cursor = size;
}

static void events_reset(void) {
    g_events_len = 0; g_events_n = 0; g_events_trunc = 0; g_events[0] = 0;
}

/* Диагностика targetGameState: гипотеза — его ActionStack хранит ПОЛНЫЙ лог
 * действий матча (вычисляется при загрузке реплея), а currentGameState —
 * лишь очередь анимаций. Пишем размеры и гистограмму классов в dump.log. */
static void target_diag(void) {
    static int last_an = -1, last_cn = -1;
    if (!ptr_ok(g_client) || !g_client_cls) return;
    void *tgt = f_ptr(g_client, g_client_cls, "targetGameState");
    if (!ptr_ok(tgt) || !class_is(tgt, g_gamestate_cls)) return;
    int32_t cur_cmd = -1;
    f_i32(g_client, g_client_cls, "targetGameStateCurrentCommand", &cur_cmd);
    void *astack = f_ptr(tgt, g_gamestate_cls, "<ActionStack>k__BackingField");
    void *cstack = f_ptr(tgt, g_gamestate_cls, "<CommandStack>k__BackingField");
    int32_t an = list_size(astack), cn = list_size(cstack);
    logf_("target: cur_cmd=%d actions=%d commands=%d", cur_cmd, an, cn);
    if (an == last_an && cn == last_cn) return;
    last_an = an; last_cn = cn;

    struct { const char *name; int count; } hist[64];
    int nh = 0;
    void *arr = list_items(astack);
    if (ptr_ok(arr) && an > 0 && an <= 8192) {
        for (int32_t i = 0; i < an; ++i) {
            void *act = arr_at(arr, i);
            if (!ptr_ok(act)) continue;
            Il2CppClass *k = w_obj_class(act);
            const char *nm = (k && A.class_get_name(k)) ? A.class_get_name(k) : "?";
            int j = 0;
            for (; j < nh; ++j) if (!strcmp(hist[j].name, nm)) break;
            if (j < nh) hist[j].count++;
            else if (nh < 64) { hist[nh].name = nm; hist[nh].count = 1; nh++; }
        }
    }
    for (int j = 0; j < nh; ++j) logf_("  target.action %-30s x%d", hist[j].name, hist[j].count);
    nh = 0;
    arr = list_items(cstack);
    if (ptr_ok(arr) && cn > 0 && cn <= 8192) {
        for (int32_t i = 0; i < cn; ++i) {
            void *cmd = arr_at(arr, i);
            if (!ptr_ok(cmd)) continue;
            Il2CppClass *k = w_obj_class(cmd);
            const char *nm = (k && A.class_get_name(k)) ? A.class_get_name(k) : "?";
            int j = 0;
            for (; j < nh; ++j) if (!strcmp(hist[j].name, nm)) break;
            if (j < nh) hist[j].count++;
            else if (nh < 64) { hist[nh].name = nm; hist[nh].count = 1; nh++; }
        }
    }
    for (int j = 0; j < nh; ++j) logf_("  target.command %-30s x%d", hist[j].name, hist[j].count);
}

/* Одноразовая выгрузка полного журнала команд матча из targetGameState.
 * Команды — детерминированный источник: захваты (CaptureCommand), траты
 * (Train/Research/Build), границы ходов (EndTurnCommand), атаки (AttackCommand).
 * Файл commands.json рядом со снимками. */
static void dump_commands(void) {
    if (g_commands_done) return;
    if (!ptr_ok(g_client) || !g_client_cls) return;
    void *tgt = f_ptr(g_client, g_client_cls, "targetGameState");
    if (!ptr_ok(tgt) || !class_is(tgt, g_gamestate_cls)) return;
    void *cstack = f_ptr(tgt, g_gamestate_cls, "<CommandStack>k__BackingField");
    int32_t cn = list_size(cstack);
    void *arr = list_items(cstack);
    if (!ptr_ok(arr) || cn <= 0 || cn > 8192) return;
    Il2CppClass *cmdbase = find_class_pref("CommandBase");
    char path[1300];
    snprintf(path, sizeof(path), "%s/commands.json", g_outdir);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "{\"v\": 1, \"count\": %d, \"commands\": [", cn);
    int wrote = 0;
    for (int32_t i = 0; i < cn; ++i) {
        void *cmd = arr_at(arr, i);
        if (!ptr_ok(cmd)) continue;
        Il2CppClass *k = w_obj_class(cmd);
        const char *nm = (k && A.class_get_name(k)) ? A.class_get_name(k) : "?";
        uint8_t pid = 0;
        if (cmdbase) f_u8(cmd, cmdbase, "<PlayerId>k__BackingField", &pid);
        int32_t type = -1;
        f_i32(cmd, k, "<Type>k__BackingField", &type); /* есть у Train/Build/Research/Upgrade */
        fprintf(f, "%s{\"i\": %d, \"t\": \"%s\", \"p\": %u, \"type\": %d}",
                wrote ? ", " : "", (int)i, nm, (unsigned)pid, (int)type);
        wrote++;
    }
    fprintf(f, "]}\n");
    fclose(f);
    g_commands_done = 1;
    logf_("commands.json записан: %d команд", wrote);
}

/* Полный снимок: игроки (+армия, +производство городов) и события хода. */
static int build_snapshot(void *gs, int turn) {
    void *players = f_ptr(gs, g_gamestate_cls, "<PlayerStates>k__BackingField");
    if (!ptr_ok(players)) return 0;
    void *arr = list_items(players);
    int32_t n = list_size(players);
    if (!ptr_ok(arr) || n < 0 || n > 32) return 0;

    int army[256], production[256];
    scan_map(gs, army, production);

    size_t o = 0;
    o += (size_t)snprintf(g_turn_json + o, sizeof(g_turn_json) - o,
                          "{\"v\": 2, \"turn\": %d, \"actions_total\": %d, \"players\": [",
                          turn, (int)g_action_cursor);

    int wrote = 0;
    for (int i = 0; i < n; ++i) {
        void *ps = arr_at(arr, i);
        if (!ptr_ok(ps)) continue;
        uint8_t id = 0; int32_t stars = 0, cities = 0, resigned = 0;
        uint32_t score = 0, endscore = 0, kills = 0, cas = 0;
        f_u8(ps, g_playstate_cls, "<Id>k__BackingField", &id);
        f_i32(ps, g_playstate_cls, "currency", &stars);
        f_i32(ps, g_playstate_cls, "cities", &cities);
        f_i32(ps, g_playstate_cls, "resignedTurn", &resigned);
        f_u32(ps, g_playstate_cls, "score", &score);
        f_u32(ps, g_playstate_cls, "endScore", &endscore);
        f_u32(ps, g_playstate_cls, "kills", &kills);
        f_u32(ps, g_playstate_cls, "casualities", &cas);
        char name[80]; read_str(f_ptr(ps, g_playstate_cls, "<UserName>k__BackingField"), name, sizeof(name));
        int32_t tribe = 0;
        f_i32(ps, g_playstate_cls, "tribe", &tribe);
        int32_t wiped = 0;
        f_i32(ps, g_playstate_cls, "wipedAtCommandIndex", &wiped);
        int a = id < 255 ? army[id] : 0;
        int pr = id < 255 ? production[id] : 0;

        if (o + 512 >= sizeof(g_turn_json)) break;
        o += (size_t)snprintf(g_turn_json + o, sizeof(g_turn_json) - o,
                              "%s{\"id\": %u, \"name\": \"%s\", \"tribe\": %d, \"score\": %u, "
                              "\"end_score\": %u, \"stars\": %d, \"cities\": %d, \"kills\": %u, "
                              "\"casualties\": %u, \"resigned_turn\": %d, \"wiped\": %s, "
                              "\"army\": %d, \"production\": %d}",
                              wrote ? ", " : "", (unsigned)id, name, tribe, score, endscore, stars,
                              cities, kills, cas, resigned, wiped >= 0 ? "true" : "false", a, pr);
        wrote++;
        logf_("  ход %d: игрок %u «%s» племя=%d счёт=%u звёзды=%d города=%d убийства=%u "
              "потери=%u армия=%d производство=%d",
              turn, (unsigned)id, name, tribe, score, stars, cities, kills, cas, a, pr);
    }
    if (o + g_events_len + g_cities_len + g_units_len + 192 >= sizeof(g_turn_json)) {
        /* события не влезают в буфер — фиксируем усечение, снимок остаётся валидным */
        g_events[0] = 0;
        g_events_len = 0;
        g_events_trunc = 1;
    }
    snprintf(g_turn_json + o, sizeof(g_turn_json) - o,
             "], \"cities\": [%s], \"units\": [%s], \"imp_found\": %d, \"events\": [%s%s]}\n",
             g_cities_json, g_units_json, g_imp_found, g_events,
             g_events_trunc ? ",{\"t\":\"truncated\"}" : "");
    return wrote > 0;
}

/* Снимок хода берётся в момент смены CurrentTurn — тогда значения совпадают
 * с тем, что HUD показывает для этого хода (проверено сверкой с OCR-эталоном:
 * первый же опрос нового хода даёт ровно те числа). latest.json обновляется
 * всегда, чтобы видеть текущее состояние. */
static void write_snapshot(int turn, int force) {
    if (!force && turn == g_last_turn) return;
    g_last_turn = turn;
    char path[1300];
    snprintf(path, sizeof(path), "%s/snapshot-%03d.json", g_outdir, turn);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "%s", g_turn_json);
    fclose(f);
    snprintf(path, sizeof(path), "%s/latest.json", g_outdir);
    f = fopen(path, "w");
    if (f) { fprintf(f, "%s", g_turn_json); fclose(f); }
    g_written++;
    logf_("снимок записан: ход %d", turn);
}

/* ---------- главный цикл ---------- */
static void run_dump(void) {
    char p[1200];
    snprintf(p, sizeof(p), "%s/dump.log", g_outdir);
    g_log = fopen(p, "w");
    if (!g_log) return;
    logf_("dump: поток запущен, pid=%d", (int)getpid());

    /* ждём загрузки движка и даём рантайму подняться: звать il2cpp_* раньше нельзя */
    int waited = 0;
    while (!gameassembly_loaded() && waited < 1200) { usleep(100 * 1000); waited++; }
    if (!gameassembly_loaded()) { logf_("GameAssembly.so не появился за %.1f с", waited / 10.0); return; }
    int delay = 6;
    const char *dv = getenv("POLYREAD_DUMP_DELAY");
    if (dv) { delay = atoi(dv); if (delay < 1) delay = 1; }
    sleep((unsigned)delay);
    logf_("движок загружен через %.1f с, старт через %d с", waited / 10.0, delay);

    if (getenv("POLYREAD_DUMP_ALLOW_LIVE")) g_replay_only = 0;
    const char *find_only = getenv("POLYREAD_DUMP_FIND");
    int interval = 300;
    const char *iv = getenv("POLYREAD_DUMP_INTERVAL");
    if (iv) { interval = atoi(iv); if (interval < 50) interval = 50; }

    void *ga = open_gameassembly();
    int missing = ga ? resolve_all(ga) : -1;
    logf_("dump: gameassembly=%s missing=%d", ga ? "ok" : "нет", missing);
    if (!ga || missing) { logf_("не хватает API — выходим"); return; }
    /* защита от падений ставится ДО любых чтений объектов: статическое поле
     * неинициализированного класса читается по нулевому указателю */
    install_guards();

    /* Поток обязан быть известен GC: иначе первая же аллокация в метаданных
     * приводит к abort "Collecting from unknown thread". */
    if (!getenv("POLYREAD_DUMP_NO_ATTACH") && A.thread_attach) {
        Il2CppDomain *d0 = w_domain();
        void *th = d0 ? w_attach(d0) : NULL;
        logf_("thread_attach: domain=%p thread=%p", (void *)d0, th);
    }

    Il2CppDomain *dom = w_domain();
    if (!dom) { logf_("домен NULL — выходим"); return; }

    size_t nasm = 0;
    const Il2CppAssembly **asms = w_assemblies(dom, &nasm);
    for (size_t i = 0; asms && i < nasm && g_nimg < 512; ++i) {
        Il2CppImage *img = w_asm_image(asms[i]);
        if (img) g_images[g_nimg++] = img;
    }
    logf_("сборок: %zu, образов: %zu", nasm, g_nimg);

    g_gamestate_cls = find_class_all("GameState");
    g_playstate_cls = find_class_all("PlayerState");
    g_mapdata_cls = find_class_pref("MapData");
    g_tiledata_cls = find_class_pref("TileData");
    g_unitstate_cls = find_class_pref("UnitState");
    g_impstate_cls = find_class_pref("ImprovementState");
    g_actionbase_cls = find_class_pref("ActionBase");
    Il2CppClass *gm_cls = find_class_all("GameManager");
    logf_("классы: GameState=%p PlayerState=%p GameManager=%p", (void *)g_gamestate_cls,
          (void *)g_playstate_cls, (void *)gm_cls);
    logf_("классы v2: MapData=%p TileData=%p UnitState=%p ImprovementState=%p ActionBase=%p",
          (void *)g_mapdata_cls, (void *)g_tiledata_cls, (void *)g_unitstate_cls,
          (void *)g_impstate_cls, (void *)g_actionbase_cls);
    if (!g_gamestate_cls || !g_playstate_cls) return;

    /* ищем GameState: сначала статические поля, потом обход от GameManager.instance */
    char where[160] = {0};
    void *gs = gamestate_from_statics(where, sizeof(where));
    if (!gs && gm_cls) {
        FieldInfo *inst = cls_field(gm_cls, "instance");
        void *gm = NULL;
        if (inst) {
            w_static_ptr(inst, &gm);
            logf_("GameManager.instance = %p", gm);
            if (ptr_ok(gm)) {
                gs = gamestate_from_crawl(gm, g_gamestate_cls, where, sizeof(where));
            }
        }
    }
    logf_("GameState: %s", gs ? where : "не найден");
    if (!gs) {
        if (find_only) { logf_("режим FIND: объекта нет (ожидаемо в меню)"); return; }
        /* ждём: в меню объекта нет, он появится в матче/реплее */
    }

    const char *flds = getenv("POLYREAD_DUMP_FIELDS");
    if (flds && *flds) { dump_fields_mode(flds); return; }
    if (getenv("POLYREAD_DUMP_WATCH")) { watch_tacts(); return; }
    if (getenv("POLYREAD_DUMP_SWEEP")) { sweep(); return; }

    if (find_only) {
        int turn = 0;
        if (gs && snapshot(gs, &turn)) logf_("FIND: живой снимок, ход %d", turn);
        return;
    }

    time_t t_end = time(NULL) + 3600; /* максимум час */
    while (time(NULL) < t_end) {
        g_frames++;
        if (!gs || !ptr_ok(gs) || !class_is(gs, g_gamestate_cls)) {
            /* объект мог появиться позже (открыли реплей) — ищем заново раз в 2 с */
            static time_t last_lookup = 0;
            if (time(NULL) - last_lookup >= 5) { /* не чаще раза в 5 с: обход не бесплатный */
                last_lookup = time(NULL);
                gs = gamestate_from_statics(where, sizeof(where));
                if (!gs) log_gs_statics();
                if (!gs) gs = state_from_client(gm_cls, where, sizeof(where));
                if (!gs && gm_cls) {
                    FieldInfo *inst = cls_field(gm_cls, "instance");
                    void *gm = NULL;
                    if (inst) {
                        w_static_ptr(inst, &gm);
                        if (ptr_ok(gm)) gs = gamestate_from_crawl(gm, g_gamestate_cls, where,
                                                                  sizeof(where));
                    }
                }
                if (gs) logf_("GameState появился: %s", where);
            }
        } else {
            if (!replay_mode_ok()) {
                if (!g_live_noted) {
                    logf_("не реплей (клиент «%s») — снимки не пишем; "
                          "обход для отладки: POLYREAD_DUMP_ALLOW_LIVE=1",
                          g_client_class[0] ? g_client_class : "неизвестен");
                    g_live_noted = 1;
                }
                usleep((useconds_t)interval * 1000);
                continue;
            }
            if (g_live_noted) { logf_("режим реплея — снимки включены"); g_live_noted = 0; }
            /* события собираем на каждом опросе: ActionStack — живая очередь,
             * действия из неё изымаются по мере проигрывания анимаций */
            collect_events(gs);
            dump_commands();
            {
                static int diag_tick = 0;
                if (++diag_tick % 10 == 0) target_diag();
            }
            int turn = 0;
            /* тяжёлая часть (карта, снимок) — только в момент смены хода */
            if (snapshot(gs, &turn) && turn != g_last_turn) {
                if (build_snapshot(gs, turn)) { write_snapshot(turn, 1); events_reset(); }
            }
        }
        usleep((useconds_t)interval * 1000);
    }
    restore_guards();
    logf_("dump завершён: кадров=%lu, снимков=%lu, перехвачено падений=%d", g_frames, g_written,
          (int)g_faults);
    fclose(g_log);
    g_log = NULL;
}

static void *thread_main(void *arg) {
    (void)arg;
    prctl(PR_SET_NAME, "polyread-dump", 0, 0, 0);
    g_self = pthread_self();
    run_dump();
    return NULL;
}

__attribute__((constructor)) static void polyread_dump_init(void) {
    const char *env = getenv("POLYREAD_DUMP");
    if (!env || !*env) return;

    if (!getenv("POLYREAD_PROBE_FORCE")) {
        char exe[1024] = {0};
        ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
        if (n <= 0 || !strstr(exe, "Polytopia.x86_64")) return;
    }

    if (!strcmp(env, "1") || !strcmp(env, "yes")) {
        const char *home = getenv("HOME");
        snprintf(g_outdir, sizeof(g_outdir), "%s/.local/share/polytopia-analyzer/inbox",
                 home ? home : ".");
    } else {
        snprintf(g_outdir, sizeof(g_outdir), "%s", env);
    }
    char tmp[1024];
    snprintf(tmp, sizeof(tmp), "%s", g_outdir);
    for (char *q = tmp + 1; *q; ++q)
        if (*q == '/') { *q = 0; mkdir(tmp, 0755); *q = '/'; }
    mkdir(tmp, 0755);

    pthread_t th;
    if (pthread_create(&th, NULL, thread_main, NULL) == 0) pthread_detach(th);
}