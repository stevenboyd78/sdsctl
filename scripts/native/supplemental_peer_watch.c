/* SPDX-License-Identifier: MIT
 * OFFLINE PROTOTYPE. Not installed, selected by an App, or a platform launcher.
 * The trusted launcher supplies ORIGINAL handles and absolute cutoffs. This
 * program cannot authenticate their publication, provenance or placement.
 * No PID discovery, Engine calls, recovery, retries, renewed budgets or writes.
 * fd3/4: original peers; fd5: original outer; fd6: cancel read end;
 * fd7: ready write end; fd8: original outer time namespace. All others closed.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/nsfs.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/timerfd.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define NS UINT64_C(1000000000)
#define MAX_LIFETIME (UINT64_C(1500) * NS)
#define MODE "--offline-original-peer-watch-v1"
#define INGRESS_MODE "--offline-original-peer-ingress-v1"
#define PACKET "original-peer-handles-v1"
static volatile sig_atomic_t interrupted;

static void interrupt_watch(int signo) { interrupted = signo; }

static bool number(const char *text, uint64_t *value) {
    uint64_t n = 0;
    if (!*text || (*text == '0' && text[1])) return false;
    for (; *text; ++text) {
        if (*text < '0' || *text > '9' || n > (UINT64_MAX - 9) / 10)
            return false;
        n = n * 10 + (unsigned)(*text - '0');
    }
    *value = n;
    return n > 0 && n <= INT64_MAX;
}

static bool boottime(uint64_t *value) {
    struct timespec now;
    if (clock_gettime(CLOCK_BOOTTIME, &now) || now.tv_sec < 0 ||
        (uint64_t)now.tv_sec > UINT64_MAX / NS) return false;
    *value = (uint64_t)now.tv_sec * NS + (uint64_t)now.tv_nsec;
    return true;
}

/* Only own retained descriptors are inspected. No /proc/PID lookup or reopen. */
static bool pid_handle(int fd, struct stat *identity, pid_t *selected) {
    char path[40], data[1025];
    int count = snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", fd);
    if (count < 1 || count >= (int)sizeof(path) || fstat(fd, identity)) return false;
    int info = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (info < 0) return false;
    ssize_t n = read(info, data, sizeof(data));
    close(info);
    if (n < 1 || n >= (ssize_t)sizeof(data) || data[n - 1] != '\n') return false;
    data[n] = 0;
    char *pid_line = strstr(data, "\nPid:\t");
    if (!pid_line) return false;
    char *end = strchr(pid_line + 6, '\n');
    uint64_t pid = 0;
    if (!end) return false;
    *end = 0;
    if (strcmp(pid_line + 6, "-1") &&
        (!number(pid_line + 6, &pid) || pid > INT_MAX ||
         pid == (uint64_t)getpid())) return false;
    if (selected) *selected = pid ? (pid_t)pid : -1;
    /* Kernel rejects non-pidfds; signal zero neither stops nor changes a peer. */
    if (!syscall(SYS_pidfd_send_signal, fd, 0, NULL, 0)) return true;
    /* A valid original may exit DURING exec. Keep its handle so that this
     * startup loss stops the other original; never require PID rediscovery. */
    struct pollfd item = {.fd = fd, .events = POLLIN};
    return errno == ESRCH && poll(&item, 1, 0) == 1 &&
           (item.revents & POLLIN) && !(item.revents & (POLLERR | POLLNVAL));
}

static bool different(const struct stat *a, const struct stat *b) {
    return a->st_dev != b->st_dev || a->st_ino != b->st_ino;
}

static bool pipe_end(int fd, int access) {
    struct stat info;
    int flags = fcntl(fd, F_GETFL);
    return flags >= 0 && (flags & O_ACCMODE) == access && (flags & O_NONBLOCK) &&
           !fstat(fd, &info) && S_ISFIFO(info.st_mode);
}

static bool original_domain(int fd, const char *boot) {
    struct stat retained, current, children;
    if (fstat(fd, &retained) || ioctl(fd, NS_GET_NSTYPE) != CLONE_NEWTIME ||
        stat("/proc/self/ns/time", &current) ||
        stat("/proc/self/ns/time_for_children", &children) ||
        different(&retained, &current) || different(&retained, &children)) return false;
    /* Exact canonical 32 lower-case hex boot ID from the original ClockWitness. */
    if (strlen(boot) != 32) return false;
    for (size_t i = 0; i < 32; ++i)
        if (!((boot[i] >= '0' && boot[i] <= '9') ||
              (boot[i] >= 'a' && boot[i] <= 'f'))) return false;
    int boot_fd = open("/proc/sys/kernel/random/boot_id", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (boot_fd < 0) return false;
    char raw[38];
    ssize_t n = read(boot_fd, raw, sizeof(raw));
    close(boot_fd);
    if (n != 37 || raw[36] != '\n') return false;
    size_t b = 0;
    for (size_t i = 0; i < 36; ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (raw[i] != '-') return false;
        } else if (b >= 32 || raw[i] != boot[b++]) return false;
    }
    return b == 32;
}

static bool future(uint64_t deadline, uint64_t ready_by) {
    uint64_t now;
    return boottime(&now) && ready_by > now && ready_by <= deadline &&
           ready_by - now <= 2 * NS && deadline - now <= MAX_LIFETIME;
}

static bool socket_option(int option, int expected) {
    int value;
    socklen_t size = sizeof(value);
    return !getsockopt(0, SOL_SOCKET, option, &value, &size) &&
           size == sizeof(value) && value == expected;
}

/* fd0: original private socketpair endpoint; fd1: independently retained outer
 * pidfd; fd2: original time namespace. These three anchors must come from the
 * trusted launcher, NEVER from an incoming message or peer/PID discovery.
 * They fit the v256 standard-I/O descriptor API; no service is installed here.
 * Before full authentication, received handles confer NO signal authority.
 */
static bool ingress(uint64_t deadline, uint64_t ready_by, const char *boot) {
    if (syscall(SYS_close_range, 3U, UINT_MAX, 0)) return false;
    struct stat outer, ns, received_outer, received_ns;
    pid_t original_pid;
    if (!pid_handle(1, &outer, &original_pid) || original_pid < 1 ||
        !original_domain(2, boot) || fstat(2, &ns) || !future(deadline, ready_by)) return false;
    int flags = fcntl(0, F_GETFL);
    if (flags < 0 || !(flags & O_NONBLOCK) ||
        !socket_option(SO_TYPE, SOCK_SEQPACKET) || !socket_option(SO_DOMAIN, AF_UNIX) ||
        !socket_option(SO_ACCEPTCONN, 0) || !socket_option(SO_PASSCRED, 1)) return false;
    /* No address, filesystem listener, abstract name, accept or reconnect. */
    struct sockaddr_un address;
    socklen_t size = sizeof(address);
    if (getsockname(0, (struct sockaddr *)&address, &size) ||
        size != sizeof(sa_family_t) || address.sun_family != AF_UNIX) return false;
    size = sizeof(address);
    if (getpeername(0, (struct sockaddr *)&address, &size) ||
        size != sizeof(sa_family_t) || address.sun_family != AF_UNIX) return false;
    struct ucred peer;
    size = sizeof(peer);
    if (getsockopt(0, SOL_SOCKET, SO_PEERCRED, &peer, &size) || size != sizeof(peer) ||
        peer.pid != original_pid || peer.uid != geteuid() || peer.gid != getegid()) return false;

    int timer = timerfd_create(CLOCK_BOOTTIME, TFD_CLOEXEC | TFD_NONBLOCK);
    struct itimerspec value = {.it_value = {
        .tv_sec = (time_t)(ready_by / NS), .tv_nsec = (long)(ready_by % NS)}};
    if (timer < 0 || timerfd_settime(timer, TFD_TIMER_ABSTIME, &value, NULL)) return false;
    struct pollfd waiting[] = {{0, POLLIN, 0}, {1, POLLIN, 0}, {timer, POLLIN, 0}};
    char data[sizeof(PACKET)];
    union {
        struct cmsghdr alignment;
        unsigned char bytes[CMSG_SPACE(6 * sizeof(int)) + CMSG_SPACE(sizeof(struct ucred))];
    } control;
    int handles[6];
    /* Exactly one data packet, followed by write-half EOF within the ORIGINAL
     * deadline. No second attempt, trailing packet, renewed timeout or retry.
     * recvmsg is nonblocking even if another holder tampers with file flags.
     * Process exit closes received/truncated rights on every rejection path.
     */
    for (int packet = 0; packet < 2; ++packet) {
        if (poll(waiting, 3, -1) < 1 || waiting[1].revents || waiting[2].revents ||
            (waiting[0].revents & (POLLERR | POLLNVAL)) ||
            !(waiting[0].revents & (POLLIN | POLLHUP)) || !future(deadline, ready_by)) return false;
        struct iovec io = {.iov_base = data, .iov_len = sizeof(data)};
        struct msghdr msg = {.msg_iov = &io, .msg_iovlen = 1,
                            .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
        ssize_t n = recvmsg(0, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
        if (n < 0 || (msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC))) return false;
        if (packet == 1) {
            if (n != 0 || msg.msg_controllen != 0) return false;
            break;
        }
        if (n != sizeof(PACKET) - 1 || memcmp(data, PACKET, sizeof(PACKET) - 1)) return false;
        bool rights = false, credentials = false;
        for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
            if (c->cmsg_level != SOL_SOCKET) return false;
            if (c->cmsg_type == SCM_RIGHTS && !rights &&
                c->cmsg_len == CMSG_LEN(sizeof(handles))) {
                memcpy(handles, CMSG_DATA(c), sizeof(handles));
                rights = true;
            } else if (c->cmsg_type == SCM_CREDENTIALS && !credentials &&
                       c->cmsg_len == CMSG_LEN(sizeof(struct ucred))) {
                struct ucred sender;
                memcpy(&sender, CMSG_DATA(c), sizeof(sender));
                if (sender.pid != peer.pid || sender.uid != peer.uid || sender.gid != peer.gid)
                    return false;
                credentials = true;
            } else return false;
        }
        if (!rights || !credentials) return false;
    }
    /* Still the same LIVE independently retained outer; no recycled PID match.
     * Received anchors must be duplicates of the launcher anchors, not merely
     * objects of the right type. The common watcher validates the other four.
     */
    struct pollfd alive = {.fd = 1, .events = POLLIN};
    if (poll(&alive, 1, 0) != 0 || !future(deadline, ready_by) ||
        fstat(handles[2], &received_outer) || different(&outer, &received_outer) ||
        fstat(handles[5], &received_ns) || different(&ns, &received_ns)) return false;
    /* Received fd numbers need not match ABI slots. Stage away from destinations
     * before dup3; preserve shared OFD flags and never reopen a process/path. */
    int staged[6];
    for (int i = 0; i < 6; ++i) {
        staged[i] = fcntl(handles[i], F_DUPFD_CLOEXEC, 20);
        if (staged[i] < 0) return false;
    }
    close(timer);
    for (int i = 0; i < 6; ++i)
        if (dup3(staged[i], i + 3, O_CLOEXEC) < 0) return false;
    if (syscall(SYS_close_range, 9U, UINT_MAX, 0)) return false;
    return true;
}

/* All valid original targets are attempted independently. No kill(PID) fallback. */
static bool stop_originals(void) {
    bool okay = true;
    for (int fd = 3; fd <= 4; ++fd) {
        if (!syscall(SYS_pidfd_send_signal, fd, SIGKILL, NULL, 0)) continue;
        struct pollfd item = {.fd = fd, .events = POLLIN};
        if (errno != ESRCH || poll(&item, 1, 0) != 1 ||
            !(item.revents & POLLIN) || (item.revents & (POLLERR | POLLNVAL))) okay = false;
    }
    return okay;
}

static int failed(void) { stop_originals(); return 70; }

int main(int argc, char **argv) {
    _Static_assert(sizeof(time_t) >= 8, "64-bit time_t required");
    uint64_t deadline, ready_by, now;
    /* Unrecognized invocation never acquires authority over supplied fds. */
    if (argc != 5 || (strcmp(argv[1], MODE) && strcmp(argv[1], INGRESS_MODE)) ||
        !number(argv[2], &deadline) ||
        !number(argv[3], &ready_by)) return 64;
    if (!strcmp(argv[1], INGRESS_MODE) && !ingress(deadline, ready_by, argv[4])) return 64;

    struct stat identities[3], cancel, ready;
    for (int i = 0; i < 2; ++i)
        if (!pid_handle(i + 3, &identities[i], NULL)) return 64;
    if (!different(&identities[0], &identities[1])) return 64;
    /* After the exact distinct stop targets are checked, failures stop BOTH.
     * Before this commit point the caller must independently retire originals.
     * This is an ABI check, not authenticated authority or runtime qualification.
     */
    if (!pid_handle(5, &identities[2], NULL) ||
        !different(&identities[0], &identities[2]) ||
        !different(&identities[1], &identities[2]) ||
        !pipe_end(6, O_RDONLY) || !pipe_end(7, O_WRONLY) ||
        fstat(6, &cancel) || fstat(7, &ready) || !different(&cancel, &ready) ||
        !original_domain(8, argv[4]) || !boottime(&now) || ready_by <= now ||
        ready_by > deadline || ready_by - now > 2 * NS || deadline <= now ||
        deadline - now > MAX_LIFETIME) return failed();

    struct rlimit core = {0, 0};
    if (setrlimit(RLIMIT_CORE, &core) || prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) ||
        prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)) return failed();
    struct sigaction action = {.sa_handler = interrupt_watch};
    sigemptyset(&action.sa_mask);
    int signals[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2, SIGALRM};
    for (size_t i = 0; i < sizeof(signals) / sizeof(signals[0]); ++i)
        if (sigaction(signals[i], &action, NULL)) return failed();
    action.sa_handler = SIG_IGN;
    if (sigaction(SIGPIPE, &action, NULL)) return failed();
    sigset_t empty, blocked;
    sigemptyset(&empty);
    sigemptyset(&blocked);
    for (size_t i = 0; i < sizeof(signals) / sizeof(signals[0]); ++i)
        sigaddset(&blocked, signals[i]);
    if (sigprocmask(SIG_SETMASK, &blocked, NULL)) return failed();
    /* No inherited socket, input, credential, journal or cancellation writer. */
    if (syscall(SYS_close_range, 9U, UINT_MAX, 0)) return failed();
    for (int fd = 0; fd < 3; ++fd) close(fd);

    /* Private timer: parent cannot consume, cancel or re-arm it via a duplicate.
     * Copy the ORIGINAL absolute cutoff, never now + duration. */
    int timer = timerfd_create(CLOCK_BOOTTIME, TFD_CLOEXEC | TFD_NONBLOCK);
    struct itimerspec value = {.it_value = {
        .tv_sec = (time_t)(deadline / NS), .tv_nsec = (long)(deadline % NS)}};
    if (timer < 0 || timerfd_settime(timer, TFD_TIMER_ABSTIME, &value, NULL)) return failed();
    struct pollfd fds[] = {{3, POLLIN, 0}, {4, POLLIN, 0}, {5, POLLIN, 0},
                          {6, POLLIN, 0}, {timer, POLLIN, 0}};
    struct timespec immediate = {0, 0};
    if (interrupted || ppoll(fds, 5, &immediate, &empty) != 0 ||
        !boottime(&now) || now >= ready_by ||
        write(7, "1", 1) != 1) return failed();
    close(7);
    for (;;) {
        if (interrupted) return failed();
        /* Atomic mask handoff closes the signal-between-check-and-wait race. */
        int events = ppoll(fds, 5, NULL, &empty);
        if (events < 1 || interrupted) return failed();
        for (size_t i = 0; i < 5; ++i)
            if (fds[i].revents & (POLLERR | POLLNVAL)) return failed();
        if ((fds[0].revents & POLLIN) && (fds[1].revents & POLLIN)) return 0;
        int reason = fds[2].revents ? 12 : fds[3].revents ? 13 :
                     fds[4].revents ? 10 : (fds[0].revents || fds[1].revents) ? 11 : 70;
        return stop_originals() ? reason : 70;
    }
}
