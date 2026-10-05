/* OFFLINE FIXTURE ONLY: called inside the original outer. No Python executes
 * in the child. Kernel CLONE_PIDFD writes into caller-owned result memory;
 * there is no helper-to-parent report or reported PID to reopen. Not a
 * qualified installed launcher, runtime, cgroup provisioner or App grant.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/magic.h>
#include <linux/sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/vfs.h>
#include <time.h>
#include <unistd.h>

int sds_fixture_spawn(int result[2], const int handles[4], const char *stop,
                      const char *ready, const char *boot, int group, int reject) {
    if (!result || result[0] != -1 || result[1] != -1 || !handles || !stop ||
        !ready || !boot || group < -1 || reject < 0 || reject > 2) return 64;
    for (int i = 0; i < 4; ++i) {
        if (handles[i] < 32 || fcntl(handles[i], F_GETFD) < 0) return 64;
        for (int j = 0; j < i; ++j) if (handles[i] == handles[j]) return 64;
    }
    char *end = NULL;
    errno = 0;
    uint64_t cutoff = strtoull(ready, &end, 10);
    struct timespec now;
    if (errno || !end || *end || !cutoff ||
        clock_gettime(CLOCK_BOOTTIME, &now) || now.tv_sec < 0) return 64;
    uint64_t sampled = (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
    if (cutoff <= sampled || cutoff - sampled > UINT64_C(2000000000)) return 64;
    struct sigaction disposition;
    if (sigaction(SIGCHLD, NULL, &disposition) || disposition.sa_handler != SIG_DFL ||
        (disposition.sa_flags & SA_NOCLDWAIT)) return 64;
    struct clone_args args = {
        .flags = CLONE_PIDFD | CLONE_CLEAR_SIGHAND,
        .pidfd = (uint64_t)(uintptr_t)&result[1],
        .exit_signal = SIGCHLD,
    };
    if (group >= 0) {
        struct statfs filesystem;
        if (fstatfs(group, &filesystem) || filesystem.f_type != CGROUP2_SUPER_MAGIC)
            return 64;
        args.flags |= CLONE_INTO_CGROUP;
        args.cgroup = (unsigned int)group;
    }
    char *native[] = {"supplemental-peer-watch", "--offline-original-peer-ingress-v1",
                       (char *)stop, (char *)ready, (char *)boot, NULL};
    char *empty[] = {NULL};
    long child = syscall(SYS_clone3, &args, sizeof(args));
    if (child < 0) return errno == ENOSYS ? 77 : 75;  /* No fallback or retry. */
    if (child == 0) {
        /* Only native syscalls and _exit until exec. No interpreter callback,
         * allocator, atfork handler, stdio or library cleanup in this child. */
        for (int i = 0; i < 4; ++i)
            if (syscall(SYS_dup3, handles[i], i == 3 ? 9 : i, 0) < 0) _exit(76);
        if (syscall(SYS_close_range, 3U, 8U, 0) ||
            syscall(SYS_close_range, 10U, UINT_MAX, 0)) _exit(76);
        uint64_t mask = 0;
        if (syscall(SYS_rt_sigprocmask, SIG_SETMASK, &mask, NULL, sizeof(mask))) _exit(76);
        (void)syscall(SYS_execveat, 9, "", native, empty, AT_EMPTY_PATH);
        _exit(76);
    }
    /* Both outputs remain in the original owner even on a rejected return or
     * a pending Python exception before its wrapper adopts OriginalChild. */
    if (child > INT_MAX || result[1] < 0) return 75;
    result[0] = (int)child;
    /* Test-only interruption of THIS calling thread after kernel creation but
     * before ctypes can deliver a return value. Never signal a target by PID. */
    if (reject == 2 && syscall(SYS_tgkill, getpid(), syscall(SYS_gettid), SIGUSR1)) return 75;
    return reject ? 75 : 0;
}
