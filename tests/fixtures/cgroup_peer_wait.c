/* OFFLINE FIXTURE ONLY: one disposable peer with no protocol or arguments. */
#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <stdint.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 1 || !argv || !argv[0]) return 64;
    sigset_t empty;
    if (sigemptyset(&empty) || sigprocmask(SIG_SETMASK, &empty, NULL)) return 65;
    for (;;) pause();
}
