/* OFFLINE FIXTURE ONLY: birth one disposable executable in an exact fresh
 * cgroup.  The kernel writes the child's pidfd into caller-owned memory; no
 * numeric-PID migration, reopening, manager fallback or installed selector is
 * involved.  The child performs only syscalls until exec.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/magic.h>
#include <linux/sched.h>
#include <signal.h>
#include <stdint.h>
#include <sys/syscall.h>
#include <sys/vfs.h>
#include <unistd.h>

int sds_fixture_exec_in_cgroup(int result[2], int executable, int group,
                               const int *handles, int handle_count,
                               char *const argv[]) {
    if (!result || result[0] != -1 || result[1] != -1 || executable < 32 ||
        group < 32 || handle_count < 0 || handle_count > 4 || !argv ||
        !argv[0] || fcntl(executable, F_GETFD) < 0 ||
        fcntl(group, F_GETFD) < 0) return 64;
    struct statfs filesystem;
    if (fstatfs(group, &filesystem) || filesystem.f_type != CGROUP2_SUPER_MAGIC)
        return 64;
    for (int i = 0; i < handle_count; ++i) {
        if (!handles || handles[i] < 32 || fcntl(handles[i], F_GETFD) < 0)
            return 64;
        for (int j = 0; j < i; ++j)
            if (handles[i] == handles[j]) return 64;
    }
    struct sigaction disposition;
    if (sigaction(SIGCHLD, NULL, &disposition) ||
        disposition.sa_handler != SIG_DFL ||
        (disposition.sa_flags & SA_NOCLDWAIT)) return 64;
    struct clone_args args = {
        .flags = CLONE_PIDFD | CLONE_CLEAR_SIGHAND | CLONE_INTO_CGROUP,
        .pidfd = (uint64_t)(uintptr_t)&result[1],
        .exit_signal = SIGCHLD,
        .cgroup = (uint64_t)(unsigned int)group,
    };
    char *empty[] = {NULL};
    long child = syscall(SYS_clone3, &args, sizeof(args));
    if (child < 0) return errno == ENOSYS ? 77 : 75;
    if (child == 0) {
        for (int i = 0; i < handle_count; ++i)
            if (syscall(SYS_dup3, handles[i], 3 + i, 0) < 0) _exit(76);
        if (syscall(SYS_dup3, executable, 9, 0) < 0) _exit(76);
        if (handle_count < 4 &&
            syscall(SYS_close_range, (unsigned int)(3 + handle_count), 8U, 0))
            _exit(76);
        if (syscall(SYS_close_range, 10U, UINT_MAX, 0)) _exit(76);
        uint64_t mask = 0;
        if (syscall(SYS_rt_sigprocmask, SIG_SETMASK, &mask, NULL, sizeof(mask)))
            _exit(76);
        (void)syscall(SYS_execveat, 9, "", argv, empty, AT_EMPTY_PATH);
        _exit(76);
    }
    if (child > INT_MAX || result[1] < 0) return 75;
    result[0] = (int)child;
    return 0;
}
