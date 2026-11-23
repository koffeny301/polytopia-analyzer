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
 *   POLYREAD_TIMESCALE=10      → ускорение проигрывания через Unity
 *                                Time.timeScale. ВНИМАНИЕ: в текущей сборке
 *                                игры set_timeScale вырезан стриппингом IL2CPP
 *                                — механизм автоматически отключается и ждет
 *                                обновлений игры.
 *   POLYREAD_DUMP_METHODS="ReplayClient,Time" → разведка методов классов
 *                                (пишет methods-<Класс>.txt и завершается)
 *   POLYREAD_SEEK=1            → ЭКСПЕРИМЕНТ: снимать ходы нативной перемоткой
 *                                SeekToCommandImmediate вместо ожидания
 *                                проигрывания (~15–25 с вместо ~200 с). Данные
 *                                байт-в-байт как при пассивном снятии, но вызовы
 *                                методов движка из своего потока небезопасны:
 *                                возможен сбой игры. При любой ошибке шим
 *                                автоматически возвращается к пассивному
 *                                снятию. Настройка через shim.env или env.
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
typedef void (*fn_class_init)(Il2CppClass *);
/* необязательное API: разведка методов и ускорение проигрывания (Time.timeScale) */
typedef Il2CppClass *(*fn_class_from_name)(const Il2CppImage *, const char *, const char *);
typedef void *(*fn_class_get_methods)(Il2CppClass *, void **);
typedef const char *(*fn_method_get_name)(const void *);
typedef uint32_t (*fn_method_get_param_count)(const void *);
typedef const Il2CppType *(*fn_method_get_param)(const void *, uint32_t);
typedef void *(*fn_runtime_invoke)(const void *, void *, void **, void **);

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
    fn_class_init class_init;
    fn_class_from_name class_from_name;
    fn_class_get_methods class_get_methods;
    fn_method_get_name method_get_name;
    fn_method_get_param_count method_get_param_count;
    fn_method_get_param method_get_param;
    fn_runtime_invoke runtime_invoke;
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
    /* необязательные символы (разведка методов, timeScale): без них не умираем */
    struct { const char *name; void **slot; } opt[] = {
        {"il2cpp_class_init", (void **)&A.class_init},
        {"il2cpp_class_from_name", (void **)&A.class_from_name},
        {"il2cpp_class_get_methods", (void **)&A.class_get_methods},
        {"il2cpp_method_get_name", (void **)&A.method_get_name},
        {"il2cpp_method_get_param_count", (void **)&A.method_get_param_count},
        {"il2cpp_method_get_param", (void **)&A.method_get_param},
        {"il2cpp_runtime_invoke", (void **)&A.runtime_invoke},
    };
    for (size_t i = 0; i < sizeof(opt) / sizeof(opt[0]); ++i) {
        *opt[i].slot = dlsym(h, opt[i].name);
        if (!*opt[i].slot) *opt[i].slot = dlsym(RTLD_DEFAULT, opt[i].name);
        if (!*opt[i].slot) logf_("нет необязательного символа %s", opt[i].name);
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

/* строка .NET (UTF-16) → UTF-8; суррогатные пары собираются */
static void read_str(void *s, char *buf, size_t cap) {
    buf[0] = 0;
    if (!ptr_ok(s) || cap < 5) return;
    int32_t len = mem_i32(s, 16); /* Il2CppString.length */
    if (len <= 0 || len > 128) return;
    size_t o = 0;
    for (int32_t i = 0; i < len; ++i) {
        uint32_t ch = mem_u16(s, 20 + (size_t)i * 2);
        if (ch >= 0xD800 && ch <= 0xDBFF && i + 1 < len) {
            uint32_t lo = mem_u16(s, 20 + (size_t)(i + 1) * 2);
            if (lo >= 0xDC00 && lo <= 0xDFFF) {
                ch = 0x10000u + ((ch - 0xD800u) << 10) + (lo - 0xDC00u);
                ++i;
            }
        }
        if (o + 4 >= cap) break;
        if (ch < 0x80) {
            buf[o++] = (char)ch;
        } else if (ch < 0x800) {
            buf[o++] = (char)(0xC0 | (ch >> 6));
            buf[o++] = (char)(0x80 | (ch & 0x3F));
        } else if (ch < 0x10000) {
            buf[o++] = (char)(0xE0 | (ch >> 12));
            buf[o++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            buf[o++] = (char)(0x80 | (ch & 0x3F));
        } else {
            buf[o++] = (char)(0xF0 | (ch >> 18));
            buf[o++] = (char)(0x80 | ((ch >> 12) & 0x3F));
            buf[o++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            buf[o++] = (char)(0x80 | (ch & 0x3F));
        }
    }
    buf[o] = 0;
}

/* экранирование строки для JSON: кавычки, backslash и управляющие символы
 * (имена игроков приходят из памяти игры — нельзя ломать ими снимок) */
static void json_escape(char *dst, size_t dstsz, const char *src) {
    size_t o = 0;
    for (const unsigned char *s = (const unsigned char *)src; *s; ++s) {
        if (*s == '"' || *s == '\\') {
            if (o + 2 >= dstsz) break;
            dst[o++] = '\\';
            dst[o++] = (char)*s;
        } else if (*s < 0x20) {
            if (o + 7 >= dstsz) break;
            o += (size_t)snprintf(dst + o, dstsz - o, "\\u%04x", (unsigned)*s);
        } else {
            if (o + 1 >= dstsz) break;
            dst[o++] = (char)*s;
        }
    }
    dst[o] = 0;
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
static void collect_events(void *gs, int turn);
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

/* разведка методов: POLYREAD_DUMP_METHODS="ReplayClient,Time" — имена методов
 * (с числом параметров) по цепочке наследования; экземпляр не нужен, только
 * метаданные. Пишет methods-<Класс>.txt рядом со снимками. */
static void dump_methods_mode(const char *list) {
    if (!A.class_get_methods || !A.method_get_name) {
        logf_("METHODS: API методов недоступно");
        return;
    }
    char buf[512];
    snprintf(buf, sizeof(buf), "%s", list);
    char *save = NULL;
    for (char *name = strtok_r(buf, ",", &save); name; name = strtok_r(NULL, ",", &save)) {
        while (*name == ' ') name++;
        Il2CppClass *k = find_class_pref(name);
        if (!k) k = find_class_all(name);
        logf_("=== МЕТОДЫ %s: %p ===", name, (void *)k);
        if (!k) continue;
        char path[1200];
        snprintf(path, sizeof(path), "%s/methods-%s.txt", g_outdir, name);
        FILE *f = fopen(path, "w");
        Il2CppClass *cur = k;
        for (int depth = 0; cur && depth < 10; ++depth) {
            const char *cn = A.class_get_name(cur);
            if (f) fprintf(f, "--- %s ---\n", cn ? cn : "?");
            void *it = NULL;
            for (;;) {
                void *m = A.class_get_methods(cur, &it);
                if (!m) break;
                const char *mn = A.method_get_name(m);
                uint32_t np = A.method_get_param_count ? A.method_get_param_count(m) : 0;
                if (!mn) continue;
                /* сигнатура: имя + типы параметров */
                char sig[256];
                int so = snprintf(sig, sizeof(sig), "%s(", mn);
                for (uint32_t pi = 0; pi < np && so < (int)sizeof(sig) - 24; ++pi) {
                    const Il2CppType *pt = A.method_get_param ? A.method_get_param(m, pi) : NULL;
                    char *tn = pt ? A.type_get_name(pt) : NULL;
                    so += snprintf(sig + so, sizeof(sig) - (size_t)so, "%s%s",
                                   pi ? ", " : "", tn ? tn : "?");
                }
                snprintf(sig + so, sizeof(sig) - (size_t)so, ")");
                logf_("  %s::%s", cn ? cn : "?", sig);
                if (f) fprintf(f, "%s\n", sig);
            }
            cur = w_parent(cur);
        }
        if (f) fclose(f);
    }
}

/* ---------- ускорение проигрывания реплея (Unity Time.timeScale) ----------
 * Реплей — детерминированная симуляция: состояние существует только когда
 * движок его просчитал, «перемотать в конец» нельзя. Но проигрывание идёт в
 * игровом времени — подняв Time.timeScale, ускоряем его в N раз. Ставим только
 * на время реплея (replay_mode_ok), возвращаем 1.0 при выходе из реплея/меню
 * и в конце потока. */
static Il2CppClass *g_time_cls = NULL;
static const void *g_m_set_ts = NULL;
static float g_ts_want = 0.0f;
static int g_ts_applied = 0;

static void timescale_init(void) {
    g_ts_want = 10.0f; /* по умолчанию реплей в 10 раз быстрее */
    const char *ev = getenv("POLYREAD_TIMESCALE");
    if (ev) {
        g_ts_want = (float)atof(ev);
        if (g_ts_want < 1.0f) g_ts_want = 0.0f;   /* 0/мусор — выключено */
        if (g_ts_want > 100.0f) g_ts_want = 100.0f;
    }
    if (g_ts_want <= 0.0f) { logf_("timeScale: выключено"); return; }
    if (!A.class_from_name || !A.class_get_methods || !A.runtime_invoke) {
        logf_("timeScale: нет API — ускорение выключено");
        g_ts_want = 0.0f;
        return;
    }
    for (size_t i = 0; i < g_nimg; ++i) {
        const char *iname = A.image_get_name(g_images[i]);
        if (!iname || strncmp(iname, "UnityEngine.CoreModule", 22) != 0) continue;
        Il2CppClass *t = A.class_from_name(g_images[i], "UnityEngine", "Time");
        if (t) { g_time_cls = t; break; }
    }
    if (!g_time_cls) { logf_("timeScale: UnityEngine.Time не найден"); g_ts_want = 0.0f; return; }
    if (A.class_init && G_TRY) { A.class_init(g_time_cls); g_guard = 0; } else g_guard = 0;
    void *it = NULL;
    int ncnt = 0;
    for (;;) {
        void *m = A.class_get_methods(g_time_cls, &it);
        if (!m) break;
        const char *mn = A.method_get_name(m);
        if (!mn) continue;
        if (ncnt < 40) logf_("  Time.method: %s", mn);
        ncnt++;
        if (!strcmp(mn, "set_timeScale") &&
            (!A.method_get_param_count || A.method_get_param_count(m) == 1)) {
            g_m_set_ts = m;
        }
    }
    logf_("timeScale: Time=%p методов=%d set_timeScale=%p цель=%.1f",
          (void *)g_time_cls, ncnt, g_m_set_ts, g_ts_want);
    if (!g_m_set_ts) g_ts_want = 0.0f;
}

static void timescale_set(float v) {
    if (!g_m_set_ts) return;
    if (G_TRY) {
        void *args[1] = { &v };
        A.runtime_invoke(g_m_set_ts, NULL, args, NULL);
        g_guard = 0;
        logf_("timeScale -> %.2f", v);
    } else g_guard = 0;
}

static void timescale_enter(void) {
    if (g_ts_want > 1.0f && !g_ts_applied) { timescale_set(g_ts_want); g_ts_applied = 1; }
}

static void timescale_leave(void) {
    if (g_ts_applied) { timescale_set(1.0f); g_ts_applied = 0; }
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
                    collect_events(gs, turn);
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
                /* список юнитов: [id,owner,health,type,x,y] — для дельт урона (M14) */
                if (g_units_n < 256 && g_units_len + 64 < sizeof(g_units_json)) {
                    uint32_t uid = 0; uint16_t hp = 0; int32_t utype = -1;
                    f_u32(unit, g_unitstate_cls, "id", &uid);
                    f_u16(unit, g_unitstate_cls, "health", &hp);
                    f_i32(unit, g_unitstate_cls, "type", &utype);
                    wc2 uc = {0, 0};
                    f_wc(tile, g_tiledata_cls, "coordinates", &uc);
                    g_units_len += (size_t)snprintf(g_units_json + g_units_len,
                                                    sizeof(g_units_json) - g_units_len,
                                                    "%s[%u,%u,%u,%d,%d,%d]",
                                                    g_units_n ? "," : "", uid,
                                                    (unsigned)own, (unsigned)hp, (int)utype,
                                                    uc.x, uc.y);
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
 * однозначно отнести события к ходу. Вложенные subActions обходятся рекурсивно.
 *
 * Надёжный перехват (M14): ActionStack — живая очередь, элементы изымаются
 * СЛЕВА, поэтому индексный курсор терял до 56% действий (сверка с цензом
 * AttackCommand из commands.json). Теперь — полный скан очереди на каждом
 * опросе + дедупликация по отпечатку (указатель + ключевые поля, кольцо).
 * Каждое уникальное действие верхнего уровня также пишется в actions.json
 * с номером хода в момент поимки (точный источник для урона: AttackAction
 * несёт Damage/Origin/Target; targetGameState.ActionStack пуст — проверено). */
static int32_t g_action_cursor = 0;
static void *g_events_gs = NULL;
static char g_events[32768];
static size_t g_events_len = 0;
static int g_events_n = 0, g_events_trunc = 0;
static int g_commands_done = 0;

#define SEEN_N 16384  /* степень двойки; слот с ptr==NULL свободен */
typedef struct { void *ptr; uint8_t pid; int32_t a, b, c, d, e; } act_fp;
static act_fp g_seen[SEEN_N];

static unsigned seen_hash(const act_fp *f) {
    uint64_t h = (uint64_t)(uintptr_t)f->ptr * 0x9E3779B97F4A7C15ull;
    h ^= (uint64_t)(uint32_t)f->a * 0xC2B2AE3D27D4EB4Full;
    h ^= ((uint64_t)(uint32_t)f->b << 20) ^ ((uint64_t)(uint32_t)f->c << 10)
       ^ ((uint64_t)(uint32_t)f->d << 30) ^ ((uint64_t)(uint32_t)f->e << 40);
    h ^= (uint64_t)f->pid << 56;
    return (unsigned)((h ^ (h >> 29)) & (SEEN_N - 1));
}

/* возвращает 1, если действие уже учтено; иначе регистрирует отпечаток.
 * Открытая адресация, до 8 проб; при переполнении кластера считаем новым
 * (лучше редкий дубль в журнале, чем потерянное действие). */
static int seen_check_put(const act_fp *fp) {
    unsigned i = seen_hash(fp);
    for (int probe_n = 0; probe_n < 8; ++probe_n, i = (i + 1) & (SEEN_N - 1)) {
        act_fp *s = &g_seen[i];
        if (!s->ptr) { *s = *fp; return 0; }
        if (s->ptr == fp->ptr && s->pid == fp->pid && s->a == fp->a
            && s->b == fp->b && s->c == fp->c && s->d == fp->d && s->e == fp->e)
            return 1;
    }
    return 0;
}
static void seen_reset(void) { memset(g_seen, 0, sizeof(g_seen)); }

/* полный журнал действий: накапливается в памяти, атомарно пишется в
 * $OUTDIR/actions.json при каждом пополнении */
static char g_acts[2 * 1024 * 1024];
static size_t g_acts_len = 0;
static int g_acts_n = 0, g_acts_dirty = 0, g_acts_trunc = 0;

static void act_log(int i, const char *cls, int turn, unsigned pid, const char *extra, int depth) {
    if (g_acts_trunc) return;
    int w = snprintf(g_acts + g_acts_len, sizeof(g_acts) - g_acts_len,
                     "%s{\"i\":%d,\"c\":\"%s\",\"turn\":%d,\"p\":%u,\"d\":%d%s}",
                     g_acts_n ? "," : "", i, cls, turn, pid, depth, extra);
    if (w < 0 || (size_t)w >= sizeof(g_acts) - g_acts_len) { g_acts_trunc = 1; return; }
    g_acts_len += (size_t)w;
    g_acts_n++;
    g_acts_dirty = 1;
}

static void act_flush(void) {
    if (!g_acts_dirty) return;
    g_acts_dirty = 0;
    char path[1300], tmp[1316];
    snprintf(path, sizeof(path), "%s/actions.json", g_outdir);
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "{\"v\":1,\"count\":%d,\"actions\":[%s%s]}\n", g_acts_n, g_acts,
            g_acts_trunc ? ",{\"c\":\"truncated\"}" : "");
    fclose(f);
    rename(tmp, path);
}

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

static void emit_action(void *act, int depth, int turn) {
    if (!ptr_ok(act) || depth > 3) return;
    Il2CppClass *k = w_obj_class(act);
    const char *cn = (k && A.class_get_name(k)) ? A.class_get_name(k) : NULL;
    if (!cn) return;

    uint8_t pid = 0;
    if (g_actionbase_cls) f_u8(act, g_actionbase_cls, "<PlayerId>k__BackingField", &pid);

    act_fp fp;
    memset(&fp, 0, sizeof(fp));
    fp.ptr = act;
    fp.pid = pid;
    char extra[192]; extra[0] = 0;   /* поля для actions.json */
    char evbuf[224]; evbuf[0] = 0;   /* событие для снимка (не у всех классов) */
    int32_t v1 = 0, v2 = 0;
    uint8_t old = 0;
    wc2 org = {0, 0}, tgt = {0, 0};

    if (!strcmp(cn, "AttackAction")) {
        f_i32(act, k, "<Damage>k__BackingField", &v1);
        f_wc(act, k, "<Origin>k__BackingField", &org);
        f_wc(act, k, "<Target>k__BackingField", &tgt);
        fp.a = v1; fp.b = org.x; fp.c = org.y; fp.d = tgt.x; fp.e = tgt.y;
        snprintf(extra, sizeof(extra), ",\"dmg\":%d,\"ox\":%d,\"oy\":%d,\"tx\":%d,\"ty\":%d",
                 v1, org.x, org.y, tgt.x, tgt.y);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"attack\",\"p\":%u,\"dmg\":%d}",
                 (unsigned)pid, v1);
    } else if (!strcmp(cn, "KillUnitAction")) {
        f_wc(act, k, "<Coordinates>k__BackingField", &org);
        fp.b = org.x; fp.c = org.y;
        snprintf(extra, sizeof(extra), ",\"x\":%d,\"y\":%d", org.x, org.y);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"kill\",\"p\":%u,\"x\":%d,\"y\":%d}",
                 (unsigned)pid, org.x, org.y);
    } else if (!strcmp(cn, "CaptureCityAction")) {
        f_u8(act, k, "<OldOwnerId>k__BackingField", &old);
        f_i32(act, k, "<Score>k__BackingField", &v1);
        fp.a = old; fp.b = v1;
        snprintf(extra, sizeof(extra), ",\"old\":%u,\"score\":%d", (unsigned)old, v1);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"capture\",\"p\":%u,\"old\":%u,\"score\":%d}",
                 (unsigned)pid, (unsigned)old, v1);
    } else if (!strcmp(cn, "IncreaseCurrencyAction")) {
        f_i32(act, k, "<Amount>k__BackingField", &v1);
        fp.a = v1;
        snprintf(extra, sizeof(extra), ",\"amt\":%d", v1);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"income\",\"p\":%u,\"amt\":%d}",
                 (unsigned)pid, v1);
    } else if (!strcmp(cn, "TrainAction")) {
        f_i32(act, k, "<Cost>k__BackingField", &v1);
        f_i32(act, k, "<Type>k__BackingField", &v2);
        fp.a = v1; fp.b = v2;
        snprintf(extra, sizeof(extra), ",\"cost\":%d,\"utype\":%d", v1, v2);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"train\",\"p\":%u,\"cost\":%d,\"utype\":%d}",
                 (unsigned)pid, v1, v2);
    } else if (!strcmp(cn, "ResearchAction")) {
        f_i32(act, k, "<Cost>k__BackingField", &v1);
        f_i32(act, k, "<Type>k__BackingField", &v2);
        fp.a = v1; fp.b = v2;
        snprintf(extra, sizeof(extra), ",\"cost\":%d,\"ttype\":%d", v1, v2);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"research\",\"p\":%u,\"cost\":%d,\"ttype\":%d}",
                 (unsigned)pid, v1, v2);
    } else if (!strcmp(cn, "UpgradeAction")) {
        f_i32(act, k, "<Cost>k__BackingField", &v1);
        fp.a = v1;
        snprintf(extra, sizeof(extra), ",\"cost\":%d", v1);
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"upgrade\",\"p\":%u,\"cost\":%d}",
                 (unsigned)pid, v1);
    } else if (!strcmp(cn, "StartTurnAction")) {
        snprintf(evbuf, sizeof(evbuf), "{\"t\":\"turn_start\"}");
    }
    /* прочие классы: только журнал actions.json (без полей) */

    /* уникальные действия учитываются на ВСЕХ уровнях вложенности: атаки и
     * перемещения живут в subActions составных действий (EndCommandAction,
     * RunDeferredAction и т.п.) — на верхнем уровне их почти не видно */
    if (!seen_check_put(&fp)) {
        g_action_cursor++;
        act_log(g_action_cursor, cn, turn, (unsigned)pid, extra, depth);
        if (evbuf[0]) ev("%s", evbuf);
    }

    void *subs = g_actionbase_cls ? f_ptr(act, g_actionbase_cls, "subActions") : NULL;
    int32_t n = list_size(subs);
    void *arr = list_items(subs);
    if (ptr_ok(arr) && n > 0 && n <= 256)
        for (int32_t i = 0; i < n; ++i) emit_action(arr_at(arr, i), depth + 1, turn);
}

/* Полный скан ActionStack на каждом опросе (вызывается из главного цикла):
 * очередь живая, элементы изымаются слева, поэтому курсор по индексу
 * не годится — вместо него дедупликация по отпечатку в seen_check_put.
 * turn — CurrentTurn на момент опроса: действия попадают в actions.json
 * с точной привязкой к ходу. */
static void collect_events(void *gs, int turn) {
    if (gs != g_events_gs) {
        g_events_gs = gs;
        g_commands_done = 0;
        g_action_cursor = 0;
        seen_reset();
        g_acts_len = 0; g_acts_n = 0; g_acts_trunc = 0; g_acts[0] = 0;
        g_acts_dirty = 1;  /* записать пустой журнал новой партии */
    }
    void *stack = f_ptr(gs, g_gamestate_cls, "<ActionStack>k__BackingField");
    int32_t size = list_size(stack);
    if (size <= 0) { act_flush(); return; }
    void *arr = list_items(stack);
    if (!ptr_ok(arr)) { act_flush(); return; }
    if (size > 512) size = 512;  /* защита от аномально большой очереди */
    for (int32_t i = 0; i < size; ++i)
        emit_action(arr_at(arr, i), 0, turn);
    act_flush();
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
    fprintf(f, "{\"v\": 2, \"count\": %d, \"commands\": [", cn);
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
        fprintf(f, "%s{\"i\": %d, \"t\": \"%s\", \"p\": %u, \"type\": %d",
                wrote ? ", " : "", (int)i, nm, (unsigned)pid, (int)type);
        /* M14: id юнитов и координаты — для детерминированной реконструкции
         * урона (кто/кого/где атаковал, где лечился/расформирован/взорван) */
        if (!strcmp(nm, "AttackCommand")) {
            uint32_t uid = 0; wc2 org = {0,0}, tgt = {0,0};
            f_u32(cmd, k, "<UnitId>k__BackingField", &uid);
            f_wc(cmd, k, "<Origin>k__BackingField", &org);
            f_wc(cmd, k, "<Target>k__BackingField", &tgt);
            fprintf(f, ", \"uid\": %u, \"ox\": %d, \"oy\": %d, \"tx\": %d, \"ty\": %d",
                    uid, org.x, org.y, tgt.x, tgt.y);
        } else if (!strcmp(nm, "MoveCommand")) {
            uint32_t uid = 0; wc2 fr = {0,0}, to = {0,0};
            f_u32(cmd, k, "<UnitId>k__BackingField", &uid);
            f_wc(cmd, k, "<From>k__BackingField", &fr);
            f_wc(cmd, k, "<To>k__BackingField", &to);
            fprintf(f, ", \"uid\": %u, \"fx\": %d, \"fy\": %d, \"tx\": %d, \"ty\": %d",
                    uid, fr.x, fr.y, to.x, to.y);
        } else if (!strcmp(nm, "RecoverCommand") || !strcmp(nm, "DisbandCommand")
                || !strcmp(nm, "ExplodeCommand")) {
            wc2 c = {0,0};
            f_wc(cmd, k, "<Coordinates>k__BackingField", &c);
            fprintf(f, ", \"x\": %d, \"y\": %d", c.x, c.y);
        }
        fprintf(f, "}");
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
        char name[256]; read_str(f_ptr(ps, g_playstate_cls, "<UserName>k__BackingField"), name, sizeof(name));
        char name_esc[640]; json_escape(name_esc, sizeof(name_esc), name);
        int32_t tribe = 0;
        f_i32(ps, g_playstate_cls, "tribe", &tribe);
        int32_t wiped = 0;
        f_i32(ps, g_playstate_cls, "wipedAtCommandIndex", &wiped);
        int a = id < 255 ? army[id] : 0;
        int pr = id < 255 ? production[id] : 0;

        if (o + 1024 >= sizeof(g_turn_json)) break;
        o += (size_t)snprintf(g_turn_json + o, sizeof(g_turn_json) - o,
                              "%s{\"id\": %u, \"name\": \"%s\", \"tribe\": %d, \"score\": %u, "
                              "\"end_score\": %u, \"stars\": %d, \"cities\": %d, \"kills\": %u, "
                              "\"casualties\": %u, \"resigned_turn\": %d, \"wiped\": %s, "
                              "\"army\": %d, \"production\": %d}",
                              wrote ? ", " : "", (unsigned)id, name_esc, tribe, score, endscore, stars,
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
 * всегда, чтобы видеть текущее состояние.
 * Снимок хода 0 (начальное состояние ДО проигрывания) пишется как БАЗА для
 * дельт (HP юнитов, владельцы городов): импортёр не делает из него запись
 * матча (issue #1), но без него теряются приросты первого хода. */
static void write_snapshot(int turn, int force) {
    if (turn < 0) return;
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

/* ---------- эксперимент: быстрое снятие реплея через нативную перемотку ----------
 * Реплей = начальное состояние + журнал команд, анимации — лишь способ
 * проигрывания. Движок умеет перематывать симуляцию к любой команде:
 * ClientBase.SeekToCommandImmediate(UInt16, Action<Boolean>).
 * План: индексы EndTurnCommand — границы раундов; снимок хода t по образцу
 * пассивного снятия = состояние после команд раунда t, ДО его EndTurn, т.е.
 * команда ends[t-1]-1 (смещение настраивается POLYREAD_SEEK_OFFSET).
 * Вызываем из своего потока через runtime_invoke — при любом сбое/таймауте
 * откатываемся к пассивному снятию (оно продолжает работать). Включается POLYREAD_SEEK=1. */
static void *g_m_seek = NULL;
static void *g_m_skip_recap = NULL;
static void *g_m_get_am = NULL;
static void *g_am = NULL;
static void *g_m_stop_proc = NULL, *g_m_start_proc = NULL;
static int g_seek_on = 0, g_seek_done = 0, g_seek_fail = 0;
static uint16_t g_seek_ends[1024];
static int g_seek_n = 0, g_seek_step = -2, g_seek_await = -1, g_seek_grace = 0;
static int g_seek_offset = 0;
static int g_seek_exc = 0, g_seek_retries = 0, g_seek_diag = 0;
static long g_seek_pos = -1;   /* индекс команды, куда уже перемотаны */
static int g_seek_cool = 0;    /* тики до следующей перемотки */
static int g_seek_unfrozen = 0;
static int g_seek_reseek = 0;  /* повторы перемотки текущего шага */
static int g_applied_prev = -2, g_applied_stable = 0, g_applied_stall = 0;
/* Длинные прыжки SeekToCommandImmediate применяет асинхронно и «проматывает»
 * состояние к финалу (проверено: прыжок 31 команда ломал цикл 11; короткие
 * прыжки ≤22 команд дают байт-в-байт пассивное состояние). Дробим прыжок
 * промежуточными ступеньками. */
#define SEEK_MAX_HOP 16

/* позиция симуляции (ClientBase.targetGameStateCurrentCommand): растёт,
 * пока движок применяет команды (пересборка после перемотки или проигрывание).
 * Используем как индикатор занятости: дёргать SeekToCommandImmediate можно
 * только когда значение стабилизировалось (падение 23.11 — вызов во время
 * пересборки). */
static int seek_applied(void) {
    int32_t v = -1;
    if (ptr_ok(g_client) && g_client_cls)
        f_i32(g_client, g_client_cls, "targetGameStateCurrentCommand", &v);
    return (int)v;
}

/* поиск метода по имени в цепочке классов */
static void *cls_method(Il2CppClass *k, const char *name) {
    if (!A.class_get_methods) return NULL;
    for (int d = 0; k && d < 10; ++d, k = w_parent(k)) {
        void *it = NULL;
        for (;;) {
            void *m = A.class_get_methods(k, &it);
            if (!m) break;
            const char *mn = A.method_get_name(m);
            if (mn && !strcmp(mn, name)) return m;
        }
    }
    return NULL;
}

static void seek_find_methods(void) {
    if (!g_client_cls || !A.class_get_methods) return;
    g_m_seek = cls_method(g_client_cls, "SeekToCommandImmediate");
    g_m_skip_recap = cls_method(g_client_cls, "SkipRecap");
    g_m_get_am = cls_method(g_client_cls, "get_ActionManager");
    logf_("seek: Seek=%p SkipRecap=%p get_ActionManager=%p",
          g_m_seek, g_m_skip_recap, g_m_get_am);
}

/* заморозить собственную обработку команд движком: иначе после каждой
 * перемотки он «догоняет» журнал до конца и утаскивает состояние вперёд
 * (диагностика 23.11: цикл 11 стабильно уезжал в финал матча) */
static void seek_call(void *m, void *obj, void **args, const char *what);

static void seek_freeze_processing(void) {
    if (!g_m_get_am || !A.runtime_invoke) return;
    if (G_TRY) {
        g_am = A.runtime_invoke(g_m_get_am, g_client, NULL, NULL);
        g_guard = 0;
    } else { g_guard = 0; g_am = NULL; }
    if (!ptr_ok(g_am)) { logf_("seek: ActionManager недоступен"); return; }
    Il2CppClass *amk = w_obj_class(g_am);
    g_m_stop_proc = cls_method(amk, "StopProcessing");
    g_m_start_proc = cls_method(amk, "StartProcessing");
    if (g_m_stop_proc) seek_call(g_m_stop_proc, g_am, NULL, "ActionManager.StopProcessing");
    else logf_("seek: StopProcessing не найден");
}

static void seek_unfreeze_processing(void) {
    if (g_m_start_proc && ptr_ok(g_am))
        seek_call(g_m_start_proc, g_am, NULL, "ActionManager.StartProcessing");
}

/* бит маски для pid: 255 (Nature/нейтралы) и прочие большие сводим в бит 31 */
static uint32_t pid_bit(uint8_t pid) { return pid < 31 ? (1u << pid) : (1u << 31); }

static void seek_build_plan(void) {
    if (!ptr_ok(g_client) || !g_client_cls) return;
    void *tgt = f_ptr(g_client, g_client_cls, "targetGameState");
    if (!ptr_ok(tgt) || !class_is(tgt, g_gamestate_cls)) return;
    void *cstack = f_ptr(tgt, g_gamestate_cls, "<CommandStack>k__BackingField");
    int32_t cn = list_size(cstack);
    void *arr = list_items(cstack);
    if (!ptr_ok(arr) || cn <= 0 || cn > 8192) return;
    Il2CppClass *cmdbase = find_class_pref("CommandBase");

    /* проход 1: маска игроков, выпускающих EndTurnCommand (длина цикла);
     * тот же принцип, что в _command_turns импортёра */
    uint32_t end_mask = 0;
    for (int32_t i = 0; i < cn; ++i) {
        void *cmd = arr_at(arr, i);
        if (!ptr_ok(cmd)) continue;
        Il2CppClass *cc = w_obj_class(cmd);
        const char *nm = cc && A.class_get_name(cc) ? A.class_get_name(cc) : NULL;
        if (!nm || strcmp(nm, "EndTurnCommand")) continue;
        uint8_t pid = 0;
        if (cmdbase) f_u8(cmd, cmdbase, "<PlayerId>k__BackingField", &pid);
        end_mask |= pid_bit(pid);
    }
    if (!end_mask) return;

    /* проход 2: цель каждого цикла — его ПОСЛЕДНИЙ EndTurn (полная маска).
     * Диагностика 23.11: состояние после него в точности совпадает с
     * пассивным снимком этого хода; после промежуточных EndTurn движок
     * преждевременно применяет команды следующего раунда. */
    uint32_t seen = 0;
    g_seek_n = 0;
    for (int32_t i = 0; i < cn && g_seek_n < 1024; ++i) {
        void *cmd = arr_at(arr, i);
        if (!ptr_ok(cmd)) continue;
        Il2CppClass *cc = w_obj_class(cmd);
        const char *nm = cc && A.class_get_name(cc) ? A.class_get_name(cc) : NULL;
        if (!nm || strcmp(nm, "EndTurnCommand")) continue;
        uint8_t pid = 0;
        if (cmdbase) f_u8(cmd, cmdbase, "<PlayerId>k__BackingField", &pid);
        seen |= pid_bit(pid);
        if (seen == end_mask) {
            g_seek_ends[g_seek_n++] = (uint16_t)i;
            seen = 0;
        }
    }
    if (g_seek_n > 0) {
        g_seek_step = 0;
        logf_("seek: план — %d циклов (маска игроков 0x%x) из %d команд, offset=%d",
              g_seek_n, end_mask, cn, g_seek_offset);
    }
}

static void seek_call(void *m, void *obj, void **args, const char *what) {
    if (!m || !ptr_ok(obj)) return;
    if (G_TRY) {
        void *exc = NULL;
        A.runtime_invoke(m, obj, args, &exc);
        g_guard = 0;
        if (exc) {
            Il2CppClass *ec = w_obj_class((Il2CppObject *)exc);
            logf_("seek: %s — ИСКЛЮЧЕНИЕ %s", what,
                  ec && A.class_get_name(ec) ? A.class_get_name(ec) : "?");
            g_seek_exc = 1;
        } else logf_("seek: %s — вызван", what);
    } else {
        g_guard = 0;
        logf_("seek: %s — ПАДЕНИЕ при вызове, seek отключён", what);
        g_seek_fail = 1;
    }
}

static void seek_invoke_idx(long idx, const char *what) {
    if (idx < 0) idx = 0;
    uint16_t u = (uint16_t)idx;
    void *args[2] = { &u, NULL };
    seek_call(g_m_seek, g_client, args, what);
}

/* Возвращает 1, если тик потреблён seek-режимом (пассивную запись пропустить).
 * Шаги: -1 = базовый снимок (перемотка в начало), 0..n-1 = границы по всем
 * EndTurnCommand; снимок пишется только когда turn вырос (граница раунда),
 * внутрираундовые EndTurn просто пропускаются. Seek синхронный (Immediate),
 * поэтому ожидание — не более одного дополнительного тика. */
static int seek_tick(void **gs_p, Il2CppClass *gm_cls, char *where, size_t cap) {
    if (g_seek_fail) {
        /* разморозить проигрывание, чтобы пассивный режим мог доснять */
        if (!g_seek_unfrozen) { g_seek_unfrozen = 1; seek_unfreeze_processing(); }
        return 0;
    }
    if (!g_m_seek) {
        if (!ptr_ok(g_client)) return 0;   /* клиент ещё не найден — пассивно */
        seek_find_methods();
        if (!g_m_seek) {
            logf_("seek: метод не найден — остаётся пассивное снятие");
            g_seek_fail = 1;
            return 0;
        }
    }
    if (g_seek_step < -1) {
        seek_build_plan();
        if (g_seek_step < -1) return 0;    /* журнал команд ещё не готов */
        /* SkipRecap НЕ зовем (по умолчанию): он снимает анимационное
         * торможение, и движок гонит журнал команд вперёд быстрее наших
         * перемоток (состояние уезжало к финалу на поздних циклах).
         * При анимационном темпе (~16 с/раунд) окно чтения 450 мс безопасно.
         * Включить для экспериментов: POLYREAD_SEEK_SKIPRECAP=1 */
        if (g_m_skip_recap && getenv("POLYREAD_SEEK_SKIPRECAP"))
            seek_call(g_m_skip_recap, g_client, NULL, "SkipRecap");
        seek_freeze_processing();
        g_seek_exc = 0;
        g_seek_step = -1;
        return 1;
    }

    /* после перемотки объект GameState может быть заменён — каждый тик
     * перечитываем его из клиента, иначе читаем устаревший снимок */
    void *fresh0 = state_from_client(gm_cls, where, cap);
    if (fresh0) *gs_p = fresh0;
    void *gs = *gs_p;
    int turn = 0;
    int have = (ptr_ok(gs) && class_is(gs, g_gamestate_cls)) ? snapshot(gs, &turn) : 0;
    if (!have) return 1;

    if (g_seek_step == -1) {
        /* базовый снимок из initialGameState — детерминированно, без гонки
         * с проигрыванием (повторный запуск даёт тот же snapshot-000) */
        void *ig = f_ptr(g_client, g_client_cls, "initialGameState");
        if (ptr_ok(ig) && class_is(ig, g_gamestate_cls) && g_last_turn < 0) {
            if (build_snapshot(ig, 0)) {
                write_snapshot(0, 1); events_reset();
                logf_("seek: snapshot-000 записан из initialGameState");
            }
        } else {
            logf_("seek: initialGameState недоступен — снимаем без snapshot-000");
        }
        g_seek_step = 0;
        return 1;
    }
    if (g_seek_step >= g_seek_n) {
        g_seek_done = 1;
        seek_unfreeze_processing(); /* вернуть проигрывание пользователю */
        logf_("seek: снятие завершено — границ %d, последний ход %d", g_seek_n, g_last_turn);
        return 1;
    }

    if (g_seek_await < 0) {
        /* выдержка между перемотками: вызов во время обработки предыдущей
         * бросает исключение (движок занят), а в неудачный момент — роняет
         * процесс (проверено 23.11 на прыжке 114→120) */
        if (g_seek_cool > 0) { g_seek_cool--; return 1; }
        /* ждём «idle» движка: позиция симуляции не меняется 2 тика подряд.
         * Вызов во время пересборки состояния роняет процесс (23.11).
         * Если позиция ползёт дольше 40 тиков — рискуем всё равно. */
        {
            int applied = seek_applied();
            if (applied >= 0) {
                if (applied != g_applied_prev) {
                    g_applied_prev = applied;
                    g_applied_stable = 0;
                    if (++g_applied_stall < 40) return 1;
                    logf_("seek: движок не успокаивается (applied=%d) — вызываю принудительно",
                          applied);
                } else if (g_applied_stable < 2) {
                    g_applied_stable++;
                    if (g_applied_stable < 2) return 1;
                }
            }
            g_applied_stall = 0;
        }
        /* выдать перемотку: при необходимости дробим длинный прыжок на
         * ступеньки по SEEK_MAX_HOP команд (короткие применяются синхронно
         * и не «проматывают» состояние вперёд) */
        long idx = (long)g_seek_ends[g_seek_step] + g_seek_offset;
        long hop = idx;
        if (g_seek_pos >= 0 && idx - g_seek_pos > SEEK_MAX_HOP)
            hop = g_seek_pos + SEEK_MAX_HOP;
        char what[88];
        snprintf(what, sizeof(what), "SeekToCommandImmediate(%ld)%s шаг %d",
                 hop, hop == idx ? "" : " [ступенька]", g_seek_step);
        g_seek_exc = 0;
        seek_invoke_idx(hop, what);
        g_seek_cool = 2;
        if (g_seek_fail) return 0;
        if (g_seek_exc) {
            /* движок занят предыдущей перемоткой — повторим на след. тике */
            if (++g_seek_retries > 60) {
                logf_("seek: шаг %d — 60 исключений подряд, откат к пассивному снятию",
                      g_seek_step);
                g_seek_fail = 1;
                return 0;
            }
            return 1;
        }
        g_seek_retries = 0;
        g_seek_pos = hop;
        if (hop != idx) return 1;   /* ступенька: цель ещё впереди */
        /* Целевая перемотка выдана — читаем состояние В ТОМ ЖЕ тике: до
         * следующего тика движок может промотать финал реплея вперёд */
        g_seek_await = g_seek_step;
        g_seek_grace = 0;
        void *gs2 = state_from_client(gm_cls, where, cap);
        if (gs2) *gs_p = gs2;
        gs = *gs_p;
        turn = 0;
        have = (ptr_ok(gs) && class_is(gs, g_gamestate_cls)) ? snapshot(gs, &turn) : 0;
        if (!have) return 1;
        /* проваливаемся в общее решение ниже */
    } else if (++g_seek_grace > 24) {
        /* состояние так и не прочиталось — шаг вперёд без снимка */
        logf_("seek: шаг %d — состояние не читается, пропускаю", g_seek_step);
        g_seek_step++;
        g_seek_await = -1;
        g_seek_grace = 0;
        return 1;
    }

    /* ---- Единое решение по шагу: turn прочитан, метка = номер цикла ----
     * Корректная граница даёт turn ∈ {label-1, label} (диагностика 23.11:
     * на последнем EndTurn цикла поле turn запаздывает на 0..1).
     * turn > label — движок промотал реплей к финалу (финишный рывок):
     * перематываем назад; гонщик уже финишировал, обратная пересборка чистая.
     * turn < label-1 — пересборка ещё не приземлилась: ждём, потом перематываем. */
    int label = g_seek_await + 1;

    if (g_seek_diag) {
        if (++g_seek_grace >= 3) {
            if (build_snapshot(gs, turn)) write_snapshot(900 + g_seek_await, 0);
            g_seek_step++; g_seek_await = -1; g_seek_grace = 0;
        }
        return 1;
    }

    int ok = (turn >= label - 1 && turn <= label);
    if (!ok && turn > label && g_seek_reseek < 3) {
        g_seek_reseek++;
        logf_("seek: шаг %d — turn %d > %d (промчался), перематываю назад (%d)",
              g_seek_step, turn, label, g_seek_reseek);
        g_seek_await = -1; g_seek_cool = 2; g_seek_grace = 0;
        return 1;
    }
    if (!ok && turn < label - 1) {
        if (g_seek_grace < 4) return 1;            /* ждём приземления */
        if (g_seek_reseek < 3) {
            g_seek_reseek++;
            logf_("seek: шаг %d — turn %d < %d, повторяю перемотку (%d)",
                  g_seek_step, turn, label - 1, g_seek_reseek);
            g_seek_await = -1; g_seek_cool = 2; g_seek_grace = 0;
            return 1;
        }
        logf_("seek: шаг %d — пересборка не приземлилась (turn %d), пишу как есть",
              g_seek_step, turn);
    } else if (!ok) {
        logf_("seek: шаг %d — turn %d вне диапазона метки %d, пишу как есть",
              g_seek_step, turn, label);
    }

    if (label != g_last_turn) {
        if (build_snapshot(gs, label)) { write_snapshot(label, 1); events_reset(); }
    }
    g_seek_step++;
    g_seek_await = -1;
    g_seek_grace = 0;
    g_seek_reseek = 0;
    return 1;
}

/* ---------- главный цикл ---------- */
/* Конфиг-файл ~/.local/share/polytopia-analyzer/shim.env: строки KEY=***
 * Применяется через setenv только для переменных, которых нет в окружении —
 * launch-опции Steam имеют приоритет. Позволяет менять параметры сима
 * (POLYREAD_SEEK, POLYREAD_TIMESCALE, POLYREAD_DUMP_INTERVAL…) без
 * перезапуска Steam. */
static void load_config_file(void) {
    const char *home = getenv("HOME");
    if (!home) return;
    char path[1200];
    snprintf(path, sizeof(path), "%s/.local/share/polytopia-analyzer/shim.env", home);
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[512];
    int n = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *val = eq + 1;
        size_t ln = strlen(val);
        while (ln && (val[ln - 1] == '\n' || val[ln - 1] == '\r')) val[--ln] = '\0';
        if (!getenv(line) && setenv(line, val, 0) == 0) n++;
    }
    fclose(f);
    logf_("shim.env: применено переменных: %d", n);
}

static void run_dump(void) {
    char p[1200];
    snprintf(p, sizeof(p), "%s/dump.log", g_outdir);
    g_log = fopen(p, "w");
    if (!g_log) return;
    logf_("dump: поток запущен, pid=%d", (int)getpid());
    load_config_file();

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
    timescale_init();
    if (getenv("POLYREAD_SEEK") && atoi(getenv("POLYREAD_SEEK"))) {
        const char *ov = getenv("POLYREAD_SEEK_OFFSET");
        if (ov) g_seek_offset = atoi(ov);
        if (getenv("POLYREAD_SEEK_DIAG")) g_seek_diag = 1;
        if (!getenv("POLYREAD_DUMP_INTERVAL")) interval = 150; /* проверенный режим */
        g_seek_on = 1;
        logf_("seek: режим включён (offset=%d, diag=%d, interval=%d)",
              g_seek_offset, g_seek_diag, interval);
    }

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

    const char *meths = getenv("POLYREAD_DUMP_METHODS");
    if (meths && *meths) { dump_methods_mode(meths); return; }
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
            timescale_leave(); /* реплей закрыли/меню — скорость игры обычная */
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
                timescale_leave(); /* живой матч/лобби — никогда не ускоряем */
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
            if (g_seek_on && !g_seek_done &&
                seek_tick(&gs, gm_cls, where, sizeof(where))) {
                dump_commands(); /* журнал команд пишем как обычно */
                usleep((useconds_t)interval * 1000);
                continue;
            }
            timescale_enter(); /* реплей подтверждён — ускоряем проигрывание */
            int turn = 0;
            int have_turn = snapshot(gs, &turn);
            /* события собираем на каждом опросе: ActionStack — живая очередь
             * анимаций; полный скан + дедуп по отпечатку, ход — на момент поимки */
            collect_events(gs, have_turn ? turn : 0);
            dump_commands();
            {
                static int diag_tick = 0;
                if (++diag_tick % 10 == 0) target_diag();
            }
            /* тяжёлая часть (карта, снимок) — только в момент смены хода;
             * ход 0 пишем как базу для дельт (импортёр не делает из него запись) */
            if (have_turn && turn >= 0 && turn != g_last_turn) {
                if (build_snapshot(gs, turn)) { write_snapshot(turn, 1); events_reset(); }
            }
        }
        usleep((useconds_t)interval * 1000);
    }
    timescale_leave();
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