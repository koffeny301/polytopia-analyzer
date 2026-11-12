CC      ?= gcc
CFLAGS  ?= -O2 -g -fPIC -Wall -Wextra -Wno-unused-parameter
LDFLAGS ?= -shared
LIBS    ?= -ldl -lpthread

all: libpolyread_probe.so

libpolyread_probe.so: polyread_probe.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LIBS)

clean:
	rm -f libpolyread_probe.so

.PHONY: all clean