<p align="right">
    <img src="https://img.shields.io/badge/clones-70-blue" alt="Clones">
    <img src="https://img.shields.io/badge/forks-6-green" alt="Clones">
<p align="center">
    <h1 align="center">GNPS Local</h1>
    <p align="center">Local optimization of GNPS for single-user, offline metabolomics analysis</p>
    <p align="center"><strong><a href="https://some-earth-2514.github.io/GNPS_Local_Documentation/">Documenation</a></strong></p>
</p>

<video controls src="https://some-earth-2514.github.io/GNPS_Local_Documentation/assets/videos/GNPS_Local_demo.mp4" title="GNPS Local Demo"></video>

GNPS Local is an offline version of the GNPS (Global Natural Products Social Molecular Networking) platform — the widely-used tool for annotating small molecules in MS/MS metabolomics data. Where the original GNPS ran on cloud servers at UC San Diego, GNPS Local runs entirely on your own computer, with no internet connection needed once it is set up.

The core workflow supported is Feature-Based Molecular Networking (FBMN). You bring your MS/MS spectra and a feature quantification table (from MZmine, XCMS, MS-DIAL, or similar), and GNPS Local builds a molecular network: a visual map in which each node is a detected feature and edges connect features with similar fragmentation patterns. Nodes that match known compounds in the spectral library are annotated automatically.

This tool is designed for researchers who already understand molecular networking concepts and want results without waiting for cloud job queues, without sharing data externally, or without an internet connection. Think of it as running the GNPS analysis server on your own laptop — same science, same outputs, fully local.

# Why should you use it?

![alt text](https://some-earth-2514.github.io/GNPS_Local_Documentation/assets/images/Performance.png)

Benchmark testing shows that consumer laptops can achieve computing performace better than GNPS with more library hits, all offline with zero network dependency while your data stays completely private.

## Original Work
This project is a derivative of [GNPS](https://gnps.ucsd.edu) ([GNPS_Workflows](https://github.com/CCMS-UCSD/GNPS_Workflows)) developed by the Dorrestein Lab at UC San Diego.