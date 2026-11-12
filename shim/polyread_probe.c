/*
 * polyread_probe.c — одноразовый пробник (веха 6.1).
 *
 * Читает ТОЛЬКО метаданные IL2CPP: сборки, классы, имена/типы полей.
 * Не трогает экземпляры объектов, не вызывает методов игры, ничего не пишет в игру.
 *
 * Включается ТОЛЬКО переменной окружения:
 *   POLYREAD_PROBE=1          → ~/.local/share/polytopia-analyzer/probe/
 *   POLYREAD_PROBE=/some/dir  → указанная папка
 *
 * Устройство (важно для безопасности):
 *   Родитель (поток в игре) НИКОГДА не вызывает il2cpp_*: он только ждёт,
 *   пока загрузится GameAssembly.so, и запускает дочерний процесс (fork).
 *   Весь опрос движка идёт в ребёнке. Ребёнок может упасть или зависнуть —
 *   это не затрагивает игру: родитель убивает его по таймауту и пробует ещё
 *   раз (с паузой), потому что рантайм может быть ещё не готов.
 *
 * Проверено на практике:
 *   - вызов il2cpp_domain_get во время il2cpp_init роняет игру
 *     ("Exclusion ranges overlap" → SIGABRT внутри il2cpp_init), поэтому
 *     ни одного вызова движка в родительском процессе;
 *   - free() на il2cpp_type_get_name() роняет процесс ("free(): invalid size"),
 *     поэтому результаты движка не освобождаем (одноразовому пробнику можно);
 *   - подмена il2cpp_init через перехват dlsym ломает инициализацию
 *     ("Failed to initialize IL2CPP"), поэтому перехвата нет.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <link.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* ---------- непрозрачные типы IL2CPP ---------- */
typedef void Il2CppDomain;
typedef void Il2CppAssembly;
typedef void Il2CppImage;
typedef void Il2CppClass;
typedef void Il2CppType;
typedef void FieldInfo;

#define FIELD_ATTRIBUTE_STATIC 0x0010

/* ---------- нужные функции движка ---------- */
typedef Il2CppDomain *(*fn_domain_get)(void);
typedef const Il2CppAssembly **(*fn_domain_get_assemblies)(const Il2CppDomain *, size_t *);
typedef const Il2CppImage *(*fn_assembly_get_image)(const Il2CppAssembly *);
typedef const char *(*fn_image_get_name)(const Il2CppImage *);
typedef const char *(*fn_image_get_filename)(const Il2CppImage *);
typedef size_t (*fn_image_get_class_count)(const Il2CppImage *);
typedef Il2CppClass *(*fn_image_get_class)(const Il2CppImage *, size_t);
typedef const char *(*fn_class_get_name)(Il2CppClass *);
typedef const char *(*fn_class_get_namespace)(Il2CppClass *);
typedef FieldInfo *(*fn_class_get_fields)(Il2CppClass *, void **);
typedef const char *(*fn_field_get_name)(FieldInfo *);
typedef const Il2CppType *(*fn_field_get_type)(FieldInfo *);
typedef int (*fn_field_get_flags)(FieldInfo *);
typedef size_t (*fn_field_get_offset)(FieldInfo *);
typedef char *(*fn_type_get_name)(const Il2CppType *);
typedef void *(*fn_thread_attach)(Il2CppDomain *);
typedef Il2CppClass *(*fn_class_from_name)(const Il2CppImage *, const char *, const char *);

static struct api {
    fn_domain_get domain_get;
    fn_domain_get_assemblies domain_get_assemblies;
    fn_assembly_get_image assembly_get_image;
    fn_image_get_name image_get_name;
    fn_image_get_filename image_get_filename;
    fn_image_get_class_count image_get_class_count;
    fn_image_get_class image_get_class;
    fn_class_get_name class_get_name;
    fn_class_get_namespace class_get_namespace;
    fn_class_get_fields class_get_fields;
    fn_field_get_name field_get_name;
    fn_field_get_type field_get_type;
    fn_field_get_flags field_get_flags;
    fn_field_get_offset field_get_offset;
    fn_type_get_name type_get_name;
    fn_thread_attach thread_attach;
    fn_class_from_name class_from_name;
} A;

static char g_outdir[1024];
static FILE *g_log;
static FILE *g_json;

/* ---------- журналы ---------- */
static void logf_(const char *fmt, ...) {
    if (!g_log) return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
}

static void json_esc(FILE *f, const char *s) {
    if (!s) { fputs("null", f); return; }
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        switch (*p) {
        case '"': fputs("\\\"", f); break;
        case '\\': fputs("\\\\", f); break;
        case '\n': fputs("\\n", f); break;
        case '\r': fputs("\\r", f); break;
        case '\t': fputs("\\t", f); break;
        default:
            if (*p < 0x20) fprintf(f, "\\u%04x", *p);
            else fputc(*p, f);
        }
    }
    fputc('"', f);
}

/* низкоуровневая строка в trace.log — из любого потока/процесса */
static void trace_line(const char *fmt, ...) {
    if (!g_outdir[0]) return;
    char buf[512];
    int k = snprintf(buf, sizeof(buf), "t=%ld ", (long)time(NULL));
    va_list ap;
    va_start(ap, fmt);
    k += vsnprintf(buf + k, sizeof(buf) - k - 2, fmt, ap);
    va_end(ap);
    if (k < 0) return;
    if (k > (int)sizeof(buf) - 2) k = (int)sizeof(buf) - 2;
    buf[k++] = '\n';
    char path[1200];
    snprintf(path, sizeof(path), "%s/trace.log", g_outdir);
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) { ssize_t w = write(fd, buf, (size_t)k); (void)w; close(fd); }
}

/* ---------- GameAssembly.so в адресном пространстве ---------- */
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

static void *open_gameassembly(char *path_out, size_t cap) {
    struct find_ctx c = {0};
    dl_iterate_phdr(phdr_cb, &c);
    if (c.found) {
        if (path_out) snprintf(path_out, cap, "%s", c.path);
        void *h = dlopen(c.path, RTLD_LAZY | RTLD_NOLOAD);
        if (h) return h;
    }
    void *h = dlopen("GameAssembly.so", RTLD_LAZY | RTLD_NOLOAD);
    if (h && path_out) snprintf(path_out, cap, "GameAssembly.so");
    return h;
}

static int resolve_all(void *h) {
    int missing = 0;
    struct { const char *name; void **slot; } tab[] = {
        {"il2cpp_domain_get", (void **)&A.domain_get},
        {"il2cpp_domain_get_assemblies", (void **)&A.domain_get_assemblies},
        {"il2cpp_assembly_get_image", (void **)&A.assembly_get_image},
        {"il2cpp_image_get_name", (void **)&A.image_get_name},
        {"il2cpp_image_get_filename", (void **)&A.image_get_filename},
        {"il2cpp_image_get_class_count", (void **)&A.image_get_class_count},
        {"il2cpp_image_get_class", (void **)&A.image_get_class},
        {"il2cpp_class_get_name", (void **)&A.class_get_name},
        {"il2cpp_class_get_namespace", (void **)&A.class_get_namespace},
        {"il2cpp_class_get_fields", (void **)&A.class_get_fields},
        {"il2cpp_field_get_name", (void **)&A.field_get_name},
        {"il2cpp_field_get_type", (void **)&A.field_get_type},
        {"il2cpp_field_get_flags", (void **)&A.field_get_flags},
        {"il2cpp_field_get_offset", (void **)&A.field_get_offset},
        {"il2cpp_type_get_name", (void **)&A.type_get_name},
        {"il2cpp_thread_attach", (void **)&A.thread_attach},
        {"il2cpp_class_from_name", (void **)&A.class_from_name},
    };
    for (size_t i = 0; i < sizeof(tab) / sizeof(tab[0]); ++i) {
        *tab[i].slot = dlsym(h, tab[i].name);
        if (!*tab[i].slot) *tab[i].slot = dlsym(RTLD_DEFAULT, tab[i].name);
        if (!*tab[i].slot) missing++;
    }
    return missing;
}

/* ---------- сам пробник (только в дочернем процессе) ---------- */
static const char *TARGETS[] = {"GameState", "GameManager", "Player", "City", "Unit",
                                "Tribe", "MapData", "GameData", "GameStateData",
                                "PlayerState", "CityState", "UnitState", "Board",
                                "MatchState", "ReplayData"};
#define NTARGETS (sizeof(TARGETS) / sizeof(TARGETS[0]))

/* Поиск класса по имени перебором: il2cpp_class_from_name не находит часть
 * классов (например, Player в PolytopiaAssembly), перебор находит всё. */
static Il2CppClass *find_class(Il2CppImage *img, const char *name, int *matches) {
    if (!img) return NULL;
    size_t n = A.image_get_class_count ? A.image_get_class_count(img) : 0;
    Il2CppClass *found = NULL;
    for (size_t i = 0; i < n; ++i) {
        Il2CppClass *k = A.image_get_class(img, i);
        if (!k) continue;
        const char *cn = A.class_get_name(k);
        if (cn && !strcmp(cn, name)) {
            if (!found) found = k;
            if (matches) (*matches)++;
        }
    }
    return found;
}

static void dump_target(Il2CppClass *k, const char *name, int first) {
    fprintf(g_json, "%s\n    {\"name\": ", first ? "" : ",");
    json_esc(g_json, name);
    if (!k) {
        fputs(", \"found\": false}", g_json);
        logf_("  класс %-14s НЕ НАЙДЕН", name);
        return;
    }
    fputs(", \"found\": true, \"namespace\": ", g_json);
    json_esc(g_json, A.class_get_namespace(k));
    fputs(", \"fields\": [", g_json);
    int n = 0, statics = 0;
    void *iter = NULL;
    for (FieldInfo *f; (f = A.class_get_fields(k, &iter)) != NULL;) {
        const char *fn = A.field_get_name(f);
        const Il2CppType *ft = A.field_get_type(f);
        const char *tn = ft ? A.type_get_name(ft) : NULL; /* НЕ free()! */
        int flags = A.field_get_flags(f);
        size_t off = A.field_get_offset(f);
        if (flags & FIELD_ATTRIBUTE_STATIC) statics++;
        fprintf(g_json, "%s\n      {\"name\": ", n ? "," : "");
        json_esc(g_json, fn);
        fputs(", \"type\": ", g_json);
        json_esc(g_json, tn ? tn : "?");
        fprintf(g_json, ", \"flags\": %d, \"offset\": %zu, \"static\": %s}", flags, off,
                (flags & FIELD_ATTRIBUTE_STATIC) ? "true" : "false");
        logf_("    %-30s %-34s flags=0x%02x off=%-4zu%s", fn ? fn : "?", tn ? tn : "?",
              flags, off, (flags & FIELD_ATTRIBUTE_STATIC) ? "  [static]" : "");
        n++;
        if (n > 4000) break;
    }
    fprintf(g_json, "\n    ], \"field_count\": %d, \"static_field_count\": %d}", n, statics);
    logf_("  класс %-14s %d полей (%d статических)", name, n, statics);
}

static void run_probe(void) {
    char p[1200];
    snprintf(p, sizeof(p), "%s/probe.log", g_outdir);
    g_log = fopen(p, "w");
    snprintf(p, sizeof(p), "%s/probe.json", g_outdir);
    g_json = fopen(p, "w");
    if (!g_log || !g_json) return;

    time_t t0 = time(NULL);
    char gapath[4096] = {0};
    void *ga = open_gameassembly(gapath, sizeof(gapath));
    int missing = ga ? resolve_all(ga) : -1;
    logf_("probe pid=%d gameassembly=%s missing=%d", (int)getpid(), gapath, missing);
    if (!ga || missing) {
        fprintf(g_json, "{\"ok\": false, \"status\": \"symbols_missing\", \"missing\": %d}\n",
                missing);
        fclose(g_json); fclose(g_log); g_json = NULL; g_log = NULL;
        return;
    }

    trace_line("resolved, calling domain_get");
    Il2CppDomain *dom = A.domain_get();
    trace_line("domain_get -> %p", (void *)dom);
    if (!dom) {
        logf_("домен ещё NULL — рантайм не готов");
        fprintf(g_json, "{\"ok\": false, \"status\": \"no_domain\"}\n");
        fclose(g_json); fclose(g_log); g_json = NULL; g_log = NULL;
        return;
    }
    /* thread_attach из чужого потока иногда валит процесс в abort() (Boehm GC),
     * а для чтения метаданных он не нужен — включается только по запросу. */
    void *th = NULL;
    if (getenv("POLYREAD_PROBE_ATTACH")) {
        trace_line("calling thread_attach");
        th = A.thread_attach ? A.thread_attach(dom) : NULL;
        trace_line("thread_attach -> %p", th);
    }
    logf_("thread_attach → %p (attach=%s)", th, th ? "да" : "нет");

    size_t nasm = 0;
    const Il2CppAssembly **asms = A.domain_get_assemblies(dom, &nasm);
    logf_("сборок: %zu", nasm);
    trace_line("assemblies=%zu", nasm);

    fprintf(g_json, "{\"ok\": true, \"status\": \"ok\", \"pid\": %d, \"elapsed_s\": %ld,\n",
            (int)getpid(), (long)(time(NULL) - t0));
    fputs(" \"assemblies\": [", g_json);

    char cls_path[1200];
    snprintf(cls_path, sizeof(cls_path), "%s/classes.txt", g_outdir);
    FILE *cf = fopen(cls_path, "w");
    long total = 0;
    int nimg = 0;
    for (size_t i = 0; asms && i < nasm; ++i) {
        const Il2CppImage *img = A.assembly_get_image(asms[i]);
        if (!img) continue;
        const char *iname = A.image_get_name(img);
        const char *ifile = A.image_get_filename(img);
        size_t nc = A.image_get_class_count ? A.image_get_class_count(img) : 0;
        if (nc > 100000) nc = 0;
        total += (long)nc;
        fprintf(g_json, "%s\n  {\"name\": ", nimg ? "," : "");
        json_esc(g_json, iname);
        fputs(", \"file\": ", g_json);
        json_esc(g_json, ifile);
        fprintf(g_json, ", \"class_count\": %zu}", nc);
        logf_("  сборка %-26s классов=%-5zu %s", iname ? iname : "?", nc, ifile ? ifile : "");
        if (cf && nc) {
            for (size_t c = 0; c < nc; ++c) {
                Il2CppClass *k = A.image_get_class(img, c);
                if (!k) break;
                const char *n = A.class_get_name(k);
                const char *ns = A.class_get_namespace(k);
                if (n) fprintf(cf, "%s\t%s\t%s\n", iname ? iname : "?", ns ? ns : "", n);
            }
        }
        nimg++;
    }
    if (cf) fclose(cf);
    fprintf(g_json, "\n ],\n \"assembly_count\": %d, \"total_classes\": %ld,\n", nimg, total);

    /* ключевые классы: сначала игровые сборки, затем все остальные */
    Il2CppImage *pref[2] = {NULL, NULL};
    for (size_t i = 0; asms && i < nasm; ++i) {
        const Il2CppImage *img = A.assembly_get_image(asms[i]);
        if (!img) continue;
        const char *iname = A.image_get_name(img);
        if (!iname) continue;
        if (!pref[0] && !strcmp(iname, "PolytopiaAssembly.dll")) pref[0] = (Il2CppImage *)img;
        if (!pref[1] && !strcmp(iname, "GameLogicAssembly.dll")) pref[1] = (Il2CppImage *)img;
    }
    logf_("игровые сборки: %s / %s", pref[0] ? "PolytopiaAssembly.dll" : "нет",
          pref[1] ? "GameLogicAssembly.dll" : "нет");

    fputs(" \"targets\": [", g_json);
    int first = 1, found_any = 0;
    for (size_t t = 0; t < NTARGETS; ++t) {
        Il2CppClass *k = NULL;
        int matches = 0;
        for (int a = 0; a < 2 && !k; ++a)
            k = find_class(pref[a], TARGETS[t], &matches);
        for (size_t i = 0; asms && i < nasm && !k; ++i)
            k = find_class((Il2CppImage *)A.assembly_get_image(asms[i]), TARGETS[t], &matches);
        if (k) found_any++;
        dump_target(k, TARGETS[t], first);
        first = 0;
        if (matches > 1) logf_("  (совпадений по имени %s: %d, берём первый)", TARGETS[t], matches);
    }
    fprintf(g_json, "\n ],\n \"targets_found\": %d, \"timestamp\": %ld}\n", found_any, (long)t0);
    logf_("ИТОГ: сборок=%d, классов=%ld, ключевых классов найдено %d/%zu", nimg, total,
          found_any, NTARGETS);
    fflush(g_json);
    fclose(g_json);
    g_json = NULL;
    logf_("готово за %ld с", (long)(time(NULL) - t0));
    fclose(g_log);
    g_log = NULL;
}

/* ---------- родитель: ждёт загрузки движка, запускает ребёнка, повторяет ---------- */
static int probe_json_ok(void) {
    char p[1200];
    static char buf[1 << 20];
    snprintf(p, sizeof(p), "%s/probe.json", g_outdir);
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    if (!strstr(buf, "\"status\": \"ok\"")) return 0;
    const char *ac = strstr(buf, "\"assembly_count\": ");
    if (!ac) return 0;
    long v = strtol(ac + strlen("\"assembly_count\": "), NULL, 10);
    return v > 0;
}

static void supervisor(void) {
    char sup[1200];
    snprintf(sup, sizeof(sup), "%s/supervisor.log", g_outdir);
    FILE *s = fopen(sup, "w");
    time_t t0 = time(NULL);

    /* ждём, пока игра загрузит GameAssembly.so (обычно 0.2–2 с) */
    int waited = 0;
    while (!gameassembly_loaded() && waited < 1200) { /* до 2 минут */
        usleep(100 * 1000);
        waited++;
    }
    if (!gameassembly_loaded()) {
        if (s) fprintf(s, "status=no_gameassembly\nждали %.1f с\n", waited / 10.0);
        if (s) fclose(s);
        return;
    }
    if (s) fprintf(s, "gameassembly_loaded_after_s=%.1f\n", waited / 10.0);

    /* IL2CPP нельзя опрашивать из fork-ребёнка: Boehm GC в копии процесса
     * считает живыми потоки, которых там нет, и il2cpp_thread_attach падает
     * в abort(). Поэтому по умолчанию работаем в своём потоке игры, но
     * (а) после паузы, когда рантайм точно поднялся, и (б) только чтением
     * метаданных, без освобождения памяти движка. Fork-режим оставлен для
     * диагностики: POLYREAD_PROBE_FORK=1. */
    if (!getenv("POLYREAD_PROBE_FORK")) {
        const char *d = getenv("POLYREAD_PROBE_DELAY");
        int delay = d ? atoi(d) : 6;
        if (delay < 1) delay = 1;
        sleep(delay);
        trace_line("direct probe start (delay=%d)", delay);
        run_probe();
        int ok = probe_json_ok();
        if (s) fprintf(s, "mode=direct delay_s=%d ok=%d status=%s total_s=%ld\n", delay, ok,
                       ok ? "ok" : "failed", (long)(time(NULL) - t0));
        trace_line("direct probe done ok=%d", ok);
        if (s) fclose(s);
        return;
    }

    const int max_attempts = 4;
    int ok = 0, attempt = 0;
    for (attempt = 1; attempt <= max_attempts && !ok; ++attempt) {
        /* пауза: рантайм инициализируется уже после загрузки .so */
        sleep(attempt == 1 ? 6 : 8);
        char pj[1200];
        snprintf(pj, sizeof(pj), "%s/probe.json", g_outdir);
        unlink(pj);

        pid_t pid = fork();
        if (pid == 0) {
            prctl(PR_SET_NAME, "polyread-probe", 0, 0, 0);
            run_probe();
            _exit(0);
        }
        if (pid < 0) {
            if (s) fprintf(s, "attempt=%d fork_failed errno=%d\n", attempt, errno);
            break;
        }
        int status = 0, ticks = 0, hung = 0;
        for (; ticks < 600; ++ticks) { /* до 60 с на попытку */
            if (waitpid(pid, &status, WNOHANG) == pid) break;
            usleep(100 * 1000);
        }
        if (ticks >= 600) { hung = 1; kill(pid, SIGKILL); waitpid(pid, &status, 0); }

        const char *how = hung ? "hung_killed"
                         : WIFSIGNALED(status)
                               ? strsignal(WTERMSIG(status))
                               : "exit";
        ok = probe_json_ok();
        if (s)
            fprintf(s, "attempt=%d child=%d result=%s code=%d ran_s=%.1f ok=%d\n", attempt,
                    (int)pid, how, WIFEXITED(status) ? WEXITSTATUS(status) : -1, ticks / 10.0,
                    ok);
        trace_line("attempt=%d child=%d result=%s ok=%d", attempt, (int)pid, how, ok);
        if (s) fflush(s);
        if (!ok) {
            /* ребёнок мог упасть из-за неготового рантайма — ждём и пробуем снова */
            if (attempt < max_attempts) sleep(2);
        }
    }
    if (s) {
        fprintf(s, "status=%s attempts=%d total_s=%ld\n", ok ? "ok" : "failed", attempt - 1,
                (long)(time(NULL) - t0));
        fclose(s);
    }
}

static void *thread_main(void *arg) {
    (void)arg;
    prctl(PR_SET_NAME, "polyread-probe", 0, 0, 0);
    supervisor();
    return NULL;
}

__attribute__((constructor)) static void polyread_init(void) {
    const char *env = getenv("POLYREAD_PROBE");
    if (!env || !*env) return;

    if (!getenv("POLYREAD_PROBE_FORCE")) {
        char exe[1024] = {0};
        ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
        if (n <= 0 || !strstr(exe, "Polytopia.x86_64")) return;
    }

    if (!strcmp(env, "1") || !strcmp(env, "yes")) {
        const char *home = getenv("HOME");
        snprintf(g_outdir, sizeof(g_outdir), "%s/.local/share/polytopia-analyzer/probe",
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