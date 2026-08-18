---
title: Instrument Catalogue Notes
version: "1.1"
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

## Flow Cytometry Core

**Aurora Spectral Analyser** — the default analyser. Spectral unmixing means you can run
crowded panels without the compensation arithmetic a conventional analyser demands. It
counts cells; it does not keep them.

**Fusion Cell Sorter** — use it when you need the cells back. Sorting is slower and dearer
than analysis, so analyse first and sort only what you will actually culture or sequence.
Book the biosafety cabinet configuration for anything human-derived.

**Helios Mass Cytometer** — metal tags instead of fluorophores, so no spectral overlap at
all and roughly forty-five parameters. Slower acquisition and the cells are consumed;
choose it when panel size genuinely defeats the Aurora.

## Histology and Pathology Core

**Axio Slide Scanner** — digitises stained slides for quantification or sharing. Scan at
20x unless you can say why 40x is needed; the files are four times the size and rarely
four times as useful.

**Cryostat CM3** — frozen sections, and the right choice when the antigen will not survive
fixation. Sections are less flat than paraffin; do not use it for morphology you intend to
publish.

**Microtome RM7** — paraffin sections for routine morphology and multiplex staining. The
better choice whenever fixation is acceptable.

**Multiplex IHC Stainer** — up to eight markers on one slide. Panel design is the hard
part and staff will not run an unvalidated panel; bring a validated one or book time to
work it out first.

## Choosing quickly

If the question is "which imaging instrument", the short answer is: fixed sample →
Confocal C2; live sample → Spinning Disk SD1; dim signal → Confocal C3; large cleared
volume → Light Sheet LS7. For anything structural at molecular resolution, Cryo-EM.

For cells in suspension: counting or phenotyping → Aurora Spectral Analyser; cells needed
back → Fusion Cell Sorter; a panel too large for either → Helios Mass Cytometer. For
tissue on a slide: fixation acceptable → Microtome RM7; antigen fixation-sensitive →
Cryostat CM3; more than two markers → Multiplex IHC Stainer; quantification or sharing →
Axio Slide Scanner.
