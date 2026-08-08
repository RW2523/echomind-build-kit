---
title: Instrument Catalogue Notes
version: "1.0"
visibility: public
---

# Instrument Catalogue Notes

Guidance on choosing between instruments. Rates and live status come from the catalogue
in the platform; this document explains what each instrument is *for*.

## Advanced Imaging Core

**Confocal C2** — the workhorse point scanner. Best for fixed samples and routine
multi-channel immunofluorescence. Slower than the spinning disk for live imaging.

**Confocal C3** — same principle as the C2 with a more sensitive detector. Choose it over
the C2 when your signal is genuinely dim; otherwise the C2 is cheaper and just as good.

**Spinning Disk SD1** — the right choice for live-cell imaging and anything faster than
roughly one frame per second. Gentler on the sample than a point scanner.

**Light Sheet LS7** — cleared whole-mount specimens and large volumes. Sample preparation
is the hard part; talk to staff before booking.

**Cryo-EM Titan** — single-particle and tomography work. Requires its own annual
certification and grid preparation booked separately.

## Genomics Core

**NovaSeq X** — high-output sequencing. Economical only when the flow cell is filled, so
submissions are batched; expect to wait for a batch to form.

**MiSeq M3** — small runs, amplicons, and quick turnarounds. The right answer for a pilot.

**Nanopore PromethION** — long reads, structural variants, and rapid turnaround. Higher
per-base error than short-read platforms; not the tool for rare variant calling.

**Bioanalyzer B4** — quality control on nucleic acids. Run it before committing samples to
a sequencing run, not after.

## Mass Spectrometry Core

**Orbitrap Exploris** — high-resolution proteomics and accurate mass work.

**Q-TOF 6546** — small-molecule and metabolomics work.

**MALDI-TOF R2** — rapid identification and imaging mass spectrometry.

## Choosing quickly

If the question is "which imaging instrument", the short answer is: fixed sample →
Confocal C2; live sample → Spinning Disk SD1; dim signal → Confocal C3; large cleared
volume → Light Sheet LS7. For anything structural at molecular resolution, Cryo-EM.
