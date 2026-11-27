# ==========================================================
# Graph-Based Routing System Simulation
# Makefile for Phase 1, Phase 2, and Phase 3
# ==========================================================

# Compiler and Flags
CXX := g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Wno-unused-variable -Wno-unused-parameter

# Directories
PHASE1_DIR := Phase-1
PHASE2_DIR := Phase-2
PHASE3_DIR := Phase-3

# Executables
PHASE1_EXE := phase1
PHASE2_EXE := phase2
PHASE3_EXE := phase3

# Common Source (e.g., driver)
DRIVER1 := Driver1.cpp
DRIVER2 := Driver2.cpp
DRIVER3 := Driver3.cpp

# Collect source files from each phase
PHASE1_SRCS := $(wildcard $(PHASE1_DIR)/*.cpp)
PHASE2_SRCS := $(wildcard $(PHASE2_DIR)/*.cpp)
PHASE3_SRCS := $(wildcard $(PHASE3_DIR)/*.cpp)

# Object files (build artifacts)
PHASE1_OBJS := $(PHASE1_SRCS:.cpp=.o)
PHASE2_OBJS := $(PHASE2_SRCS:.cpp=.o)
PHASE3_OBJS := $(PHASE3_SRCS:.cpp=.o)

# ==========================================================
# Targets
# ==========================================================

all: $(PHASE1_EXE) $(PHASE2_EXE) $(PHASE3_EXE)

# ----------------------------------------------------------
# Phase 1 Build
# ----------------------------------------------------------
$(PHASE1_EXE): $(PHASE1_OBJS) $(DRIVER1)
	@echo "🔧 Building Phase 1 executable..."
	$(CXX) $(CXXFLAGS) -o $@ $(DRIVER1) $(PHASE1_OBJS)
	@echo "✅ Built ./$(PHASE1_EXE)"

# ----------------------------------------------------------
# Phase 2 Build
# ----------------------------------------------------------
$(PHASE2_EXE): $(PHASE2_OBJS) $(DRIVER2)
	@echo "🔧 Building Phase 2 executable..."
	$(CXX) $(CXXFLAGS) -o $@ $(DRIVER2) $(PHASE2_OBJS)
	@echo "✅ Built ./$(PHASE2_EXE)"

# ----------------------------------------------------------
# Phase 3 Build
# ----------------------------------------------------------
$(PHASE3_EXE): $(PHASE3_OBJS) $(DRIVER3)
	@echo "🔧 Building Phase 3 executable..."
	$(CXX) $(CXXFLAGS) -o $@ $(DRIVER3) $(PHASE3_OBJS)
	@echo "✅ Built ./$(PHASE3_EXE)"

# ----------------------------------------------------------
# Run Commands (for convenience)
# ----------------------------------------------------------
run-phase1:
	@echo "▶ Running Phase 1..."
	./$(PHASE1_EXE) graph.json queries.json output.json

run-phase2:
	@echo "▶ Running Phase 2..."
	./$(PHASE2_EXE) graph.json queries.json output.json

run-phase3:
	@echo "▶ Running Phase 3..."
	./$(PHASE3_EXE) graph.json queries.json output.json

# ----------------------------------------------------------
# Utility Targets
# ----------------------------------------------------------
clean:
	@echo "🧹 Cleaning object files..."
	rm -f $(PHASE1_DIR)/*.o $(PHASE2_DIR)/*.o $(PHASE3_DIR)/*.o

distclean: clean
	@echo "🗑️ Removing executables..."
	rm -f $(PHASE1_EXE) $(PHASE2_EXE) $(PHASE3_EXE)

help:
	@echo "Available targets:"
	@echo "  make all           - Build all phases"
	@echo "  make phase1        - Build phase 1 only"
	@echo "  make phase2        - Build phase 2 only"
	@echo "  make phase3        - Build phase 3 only"
	@echo "  make run-phase1    - Run phase1 with sample input/output"
	@echo "  make run-phase2    - Run phase2 with sample input/output"
	@echo "  make run-phase3    - Run phase3 with sample input/output"
	@echo "  make clean         - Remove .o files"
	@echo "  make distclean     - Remove .o files and executables"

# ----------------------------------------------------------
# Phony targets
# ----------------------------------------------------------
.PHONY: all clean distclean run-phase1 run-phase2 run-phase3 help
