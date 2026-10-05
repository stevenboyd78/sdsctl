/* OFFLINE TEST FIXTURE ONLY. No cgroup provisioning/move, platform/source proof.
 * fd0/1/2: original native ingress anchors, fd9: opened native executable,
 * fd10: private report socket to our original parent. No target handles.
 * Real clone3 CLONE_PARENT/PIDFD tests the ownership join. An optional compiled
 * variant uses fd12 for birth in the caller's EXISTING cgroup, not independence.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/sched.h>
#include <linux/magic.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/vfs.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static uint64_t now_ns(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_BOOTTIME, &now) || now.tv_sec < 0) return UINT64_MAX;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

int main(int argc, char **argv) {
    if (argc != 6 || strcmp(argv[1], "--offline-parent-spawn-fixture-v1") ||
        strcmp(argv[2], "--offline-original-peer-ingress-v1")) return 64;
    char *end = NULL;
    errno = 0;
    uint64_t ready = strtoull(argv[4], &end, 10), now = now_ns();
    if (errno || !end || *end || !ready || ready <= now ||
        ready - now > UINT64_C(2000000000)) return 64;
    struct ucred original;
    socklen_t size = sizeof(original);
    if (getsockopt(10, SOL_SOCKET, SO_PEERCRED, &original, &size) ||
        size != sizeof(original) || original.pid != getppid() ||
        original.uid != geteuid() || original.gid != getegid()) return 64;
    if (syscall(SYS_close_range, 3U, 8U, 0)) return 64;
#ifdef FIXTURE_SAME_CGROUP
    struct statfs filesystem;
    if (fstatfs(12, &filesystem) || filesystem.f_type != CGROUP2_SUPER_MAGIC ||
        close(11) || syscall(SYS_close_range, 13U, UINT_MAX, 0)) return 64;
#else
    if (syscall(SYS_close_range, 11U, UINT_MAX, 0)) return 64;
#endif
    int pidfd = -1;
    struct clone_args args = {
        .flags = CLONE_PARENT | CLONE_PIDFD,
        .pidfd = (uint64_t)(uintptr_t)&pidfd,
        /* CLONE_PARENT must use exit_signal=0, no CLONE_VM/shared stack. */
    };
#ifdef FIXTURE_SAME_CGROUP
    args.flags |= CLONE_INTO_CGROUP;
    args.cgroup = 12;
#endif
    long child = syscall(SYS_clone3, &args, sizeof(args));
    if (child < 0) return errno == ENOSYS ? 77 : 75;  /* Never fall back. */
    if (child == 0) {
        close(10);
#ifdef FIXTURE_SAME_CGROUP
        close(12);
#endif
        char *native[] = {"supplemental-peer-watch", argv[2], argv[3], argv[4], argv[5], NULL};
        char *empty[] = {NULL};
        fexecve(9, native, empty);
        _exit(76);
    }
    if (pidfd < 0 || child > INT_MAX) return 75;
    int status = 75;
    siginfo_t info = {0};
    /* We received an original pidfd, but the new child belongs to OUR PARENT.
     * Its native exit may be observed here, but must not be reaped here. */
    errno = 0;
    if (waitid(P_PIDFD, (id_t)pidfd, &info, WEXITED | WNOHANG | WNOWAIT) != -1 ||
        errno != ECHILD || getppid() != original.pid || now_ns() >= ready) goto failure;
    uint32_t report[2] = {(uint32_t)child, (uint32_t)ECHILD};
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct iovec iov = {.iov_base = report, .iov_len = sizeof(report)};
    struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    struct cmsghdr *right = CMSG_FIRSTHDR(&message);
    right->cmsg_level = SOL_SOCKET;
    right->cmsg_type = SCM_RIGHTS;
    right->cmsg_len = CMSG_LEN(sizeof(pidfd));
    memcpy(CMSG_DATA(right), &pidfd, sizeof(pidfd));
    if (sendmsg(10, &message, MSG_DONTWAIT | MSG_NOSIGNAL) != (ssize_t)sizeof(report))
        goto failure;
    if (shutdown(10, SHUT_WR)) goto failure;
    status = 0; /* Transfer only. Native readiness remains independently required. */
#ifdef FIXTURE_REPORT_FAILURE
    status = 75; /* Compile-time test fault AFTER original handle transfer. */
#endif
failure:
    if (status) (void)syscall(SYS_pidfd_send_signal, pidfd, SIGKILL, NULL, 0);
    close(pidfd);
    return status;
}
