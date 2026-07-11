#!/bin/bash

# working
./sync.py logs/00000018.BIN logs/2026-05-23\ 19-06-38.tlog logs/log.mcap # --no-overlap-check

# failing
./sync.py logs/ArduPlane-GpsSensorPreArmEAHRS-00000140.BIN logs/ArduPlane-test.tlog logs/log.mcap  # --no-overlap-check
