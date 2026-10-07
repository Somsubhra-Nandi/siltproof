"""Generate simulated dumper trips and GPS traces.

Plan section 6: ~120 trips. Route each one drain -> dump site with Amazon
Location route calculation, densify to one point every 30 s with timestamps,
then inject the fraud variants from the planted-case table (detour, impossible
turnaround, slip/GPS time conflict).

Output: data/simulated/traces/*.json
"""

# TODO Day 1 (role C): routes + densification + fraud variants.
