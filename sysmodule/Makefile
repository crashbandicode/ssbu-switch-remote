# switch-remote sysmodule. Needs devkitA64 + libnx (DEVKITPRO). Builds out/exefs.nsp and the SD layout:
#   out/sd/atmosphere/contents/4200000000005352/{exefs.nsp,toolbox.json,flags/boot2.flag}
#   make            # the sysmodule
#   make test       # host tests of core/ (system g++)
ifeq ($(strip $(DEVKITPRO)),)
ifneq ($(filter-out test out/loopback clean,$(MAKECMDGOALS)),)
$(error DEVKITPRO is not set)
endif
ifeq ($(MAKECMDGOALS),)
$(error DEVKITPRO is not set)
endif
endif

TITLE_ID := 4200000000005352
NAME     := switch-remote
A64      := $(DEVKITPRO)/devkitA64/bin/aarch64-none-elf-
TOOLS    := $(DEVKITPRO)/tools/bin
LIBNX    := $(DEVKITPRO)/libnx
ARCH     := -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE
CXXFLAGS := -g -O2 -Wall -Wextra -Werror -ffunction-sections -fdata-sections -fno-rtti -fno-exceptions \
            -std=gnu++17 $(ARCH) -D__SWITCH__ -isystem $(LIBNX)/include -Icore
LDFLAGS  := -specs=$(LIBNX)/switch.specs -g $(ARCH) -Wl,--gc-sections -Wl,-Map,out/$(NAME).map -L$(LIBNX)/lib
SRCS     := source/main.cpp core/core.cpp core/service.cpp
OBJS     := $(patsubst %.cpp,out/obj/%.o,$(SRCS))
SD       := out/sd/atmosphere/contents/$(TITLE_ID)

all: $(SD)/exefs.nsp $(SD)/toolbox.json $(SD)/flags/boot2.flag

out/obj/%.o: %.cpp core/core.hpp core/service.hpp
	@mkdir -p $(dir $@)
	$(A64)g++ $(CXXFLAGS) -c $< -o $@

out/$(NAME).elf: $(OBJS)
	$(A64)g++ $(LDFLAGS) $(OBJS) -lnx -o $@

out/$(NAME).nso: out/$(NAME).elf
	$(TOOLS)/elf2nso $< $@

out/$(NAME).npdm: $(NAME).json
	@mkdir -p out
	$(TOOLS)/npdmtool $< $@

out/exefs.nsp: out/$(NAME).nso out/$(NAME).npdm
	rm -rf out/exefs && mkdir -p out/exefs
	cp out/$(NAME).nso out/exefs/main
	cp out/$(NAME).npdm out/exefs/main.npdm
	$(TOOLS)/build_pfs0 out/exefs $@

$(SD)/exefs.nsp: out/exefs.nsp
	@mkdir -p $(dir $@)
	cp $< $@

$(SD)/toolbox.json: toolbox.json
	@mkdir -p $(dir $@)
	cp $< $@

$(SD)/flags/boot2.flag:
	@mkdir -p $(dir $@)
	touch $@

test: out/loopback
	@mkdir -p out
	g++ -std=c++17 -O1 -Wall -Wextra -Werror -Icore core/core.cpp core/tests/test_core.cpp -o out/test_core
	./out/test_core

# The host loopback server (tools/host/tests use it).
out/loopback: host_loopback/loopback.cpp core/core.cpp core/service.cpp core/core.hpp core/service.hpp
	@mkdir -p out
	g++ -std=c++17 -O1 -Wall -Wextra -Werror -pthread -Icore core/core.cpp core/service.cpp host_loopback/loopback.cpp -o $@

clean:
	rm -rf out

.PHONY: all test clean
.DEFAULT_GOAL := all
