CC      ?= gcc
CFLAGS  ?= -O2 -g -fPIC -Wall -Wextra -Wno-unused-parameter
LDFLAGS ?= -shared
LIBS    ?= -ldl -lpthread

all: libpolyread_probe.so libpolyread_dump.so

libpolyread_probe.so: polyread_probe.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LIBS)

libpolyread_dump.so: polyread_dump.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LIBS)

clean:
	rm -f libpolyread_probe.so libpolyread_dump.so

.PHONY: all clean
