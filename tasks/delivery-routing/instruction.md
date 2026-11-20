you are tasked with debugging a multi-phase delivery routing engine located in /app. the codebase is currently failing tests due to logic bugs in the phase 2 pathfinding algorithms and the phase 3 scheduler. your first goal is to locate and fix the logic bugs in phase 2. your second goal is to debug the scheduler in phase 3 to pass strict thresholds on the average delivery time penalty per event on large city graphs.

the engine has two executables that are verified independently: `/app/phase2` and `/app/phase3`.

Specific logic fixes to apply in `/app`:
1. In `/app/Phase-2/approximate-shortest-paths.cpp`: remove `+ 0.0001` noise in the distance relaxation condition so `dist[v] = dist[u] + weight`.
2. In `/app/Phase-2/shortest-path.cpp`: remove the `1.5 *` factor in euclidean distance so it returns `std::sqrt(dx*dx + dy*dy)`.
3. In `/app/Phase-2/k-shortest-heurestics.cpp`: hash exact `(uint64_t)x` instead of `x % 10`.
4. In `/app/Phase-3/scheduler.cpp`: fix distance calculation to `dx * dx + dy * dy` (remove `* 100.0`), and ensure uniform time accumulation `time_so_far += best_seg` without discount on `d % 3`.
5. Compile both binaries: execute `cd /app && make clean && make phase2 && make phase3` to build `/app/phase2` and `/app/phase3`.
