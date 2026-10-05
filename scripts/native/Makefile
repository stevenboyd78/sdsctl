# Offline build only. No install, service, container, cleanup or release targets.
ifeq ($(filter /%,$(BUILD_DIR)),)
$(error Set BUILD_DIR to an explicit absolute disposable output directory)
endif

CC ?= cc
SOURCE := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))supplemental_peer_watch.c
TARGET := $(BUILD_DIR)/supplemental-peer-watch
HARDENING := -std=c11 -O2 -Wall -Wextra -Werror -Wconversion -Wshadow -Wformat=2 \
	-fstack-protector-strong -D_FORTIFY_SOURCE=3 -static-pie -Wl,-z,relro,-z,now

.PHONY: all
all: $(TARGET)

$(TARGET): $(SOURCE) $(lastword $(MAKEFILE_LIST))
	mkdir -p -- "$(BUILD_DIR)"
	$(CC) $(HARDENING) "$(SOURCE)" -o "$@"
