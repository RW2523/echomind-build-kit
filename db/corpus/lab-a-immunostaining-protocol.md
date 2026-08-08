---
title: Lab A Immunostaining Protocol
version: "4.2"
visibility: lab
lab: lab-a
---

# Lab A Immunostaining Protocol

Internal to the Patel Lab (Lab A). This is our house protocol and differs deliberately
from the generic facility guidance — do not circulate it outside the lab.

## Fixation

Fix in 4% paraformaldehyde for 12 minutes at room temperature. Longer fixation masks the
epitope for our primary antibody and is the usual cause of a weak signal in this
protocol.

## Permeabilisation and blocking

Permeabilise with 0.2% Triton X-100 for 8 minutes. Block for 45 minutes in 5% normal goat
serum. We deliberately use goat rather than donkey serum because our secondary is
goat-raised.

## Primary antibody

Our validated dilution for the anti-tubulin primary is **1:400**, incubated overnight at
4 °C. The vendor sheet suggests 1:200; at that concentration we see substantial
background in this cell line and it wastes antibody.

## Secondary antibody

1:1000 for 60 minutes at room temperature, protected from light.

## Imaging settings

Image on the Confocal C2. Our standard acquisition for this protocol:

| Parameter | Value |
|---|---|
| Objective | 63x oil, Type F |
| 488 nm laser power | 8% |
| Pinhole | 1.0 AU |
| Averaging | 2x line |

Do not exceed 12% on the 488 line with this protocol. Above that the tubulin signal
bleaches visibly within a single z-stack.

## Known issues

Batch 7 of our secondary antibody gave elevated background across three independent
experiments in February. Batch 8 onwards is fine. If you are using an old aliquot, check
the batch number before blaming the protocol.
