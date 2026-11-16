# Host builds of the switch-pad core (the ESP-IDF build is `idf.py build`, run by tools/build_pad.ps1 on the Yoga).
#   make -C firmware test       core tests
#   make -C firmware out/padsim the pty simulator used by tools/host/tests/test_usbpad.py
CXX ?= g++
CXXFLAGS = -std=c++17 -O1 -Wall -Wextra -Werror
CORE = core/pad.cpp ../sysmodule/core/core.cpp

.PHONY: test clean
test: out/test_pad out/padsim
	./out/test_pad

out/test_pad: $(CORE) core/pad.hpp core/tests/test_pad.cpp ../sysmodule/core/core.hpp
	@mkdir -p out
	$(CXX) $(CXXFLAGS) -I core -I ../sysmodule/core $(CORE) core/tests/test_pad.cpp -o $@

out/padsim: $(CORE) core/pad.hpp host_sim/padsim.cpp ../sysmodule/core/core.hpp
	@mkdir -p out
	$(CXX) $(CXXFLAGS) -I core -I ../sysmodule/core $(CORE) host_sim/padsim.cpp -o $@

clean:
	rm -rf out
