Comms test
==========

This is a simplified standalone comms tests which is a very basic model of the
communications in libensemble.

The current test, only models the commuications from workers to manager.

The test is easily configurable for size of data, rounds of communication, and
can be run on any number of processors. The data is initailsed on each worker
using the processor ID. The manager checks the correct values are received.

The data is a dictionary containing a scalar and numpy array. The size of the
numpy array is given by <array_size>. The default is one million values. The
variable <rounds> determines the number of messages sent from each worker. Two
lines can be uncommented in the manager loop which gives the message count and
size of the received message in bytes.

The test can also be used for benchmarking.

Running:

eg:
mpirun -np 64 python commtest.py
srun --ntasks 128 python commtest.py
aprun -n 256 -N 64 python commtest.py
