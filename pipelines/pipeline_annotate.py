"""
=============================
Metagenome annotation pipeline
=============================

:Author: Matt Jackson
:Tags: Python

Takes contigs from assembled metagenomic data, identfies ORFs and carrys out functional and taxonomic annotation.

Overview
========

This pipeline uses Prodigal to detect ORFs in metagenomic assemblies.
Eggnog mapper is then used to assign functional annotations to each ORF and MMSeqs2 to assign taxonomy using a lowest-common ancestor approach using the NCBI database. 

  
Usage
=====

See cgat.org for general
information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.yml` file. 


Input
-----

Reads
+++++

Reads are imported by placing files are linking to files in the
:term:`working directory`.

The default file format assumes the following convention:

   <filename>.<suffix>

The ``suffix`` determines the file type.  The
following suffixes/file types are possible:

fasta,fastq

fasta.1,fastq.1

fasta.gz,fastq.qz

fasta.1.gz,fastq.1.gz

Where unnumbered files relate to single end reads or inter-leaved paired-end files.
Interleaved files should be detected automatically.
Files containg 1 in the suffix will be assumed paired end and should be in the same 
directory as the read 2 files, which share the same file name except for the read number.

Requirements
------------

On top of the default CGAT setup and the MetaSequencing pipeline, annotation requires the following
software to be in the path:

+--------------------+-------------------+------------------------------------------------+
|*Program*           |*Tested Version*   |*Purpose*                                       |
+--------------------+-------------------+------------------------------------------------+
| Prodigal           | 2.6.3             | Detect ORFs                                    |
+--------------------+-------------------+------------------------------------------------+
| eggnog-mapper      | 1.0.3             | Functional annotation of ORFs                  |  
+--------------------+-------------------+------------------------------------------------+
| MMseqs2            | 2018-12-14        | Taxonomic asignment of  ORFs vs NCBI database  |
+--------------------+-------------------+------------------------------------------------+

Also requires suitable reference databases indexed with the appropriate software versions.

Pipeline output
===============

The main output is a set of functionally and taxonomically annotated contigs.

Glossary
========

.. glossary::

Code
====

"""

#load modules
from ruffus import *
import os
import sys, re
import subprocess

###################################################
###################################################
###################################################
# Pipeline configuration
###################################################
# load options from the config file
import cgatcore.pipeline as P
P.get_parameters(
       ["%s.yml" % __file__[:-len(".py")],
       "../pipeline.yml",
       "pipeline.yml" ] )
PARAMS = P.PARAMS

import PipelineAssembly
import PipelineAnnotate

#get all files within the directory to process
SEQUENCEFILES = ("*.fasta", "*.fasta.gz", "*.fasta.1.gz", "*.fasta.1",
                 "*.fna", "*.fna.gz", "*.fna.1.gz", "*.fna.1",
                 "*.fa", "*.fa.gz", "*.fa.1.gz", "*.fa.1", 
                 "*.fastq", "*.fastq.gz", "*.fastq.1.gz","*.fastq.1")

SEQUENCEFILES_REGEX = regex(
    r"(\S+).(fasta$|fasta.gz|fasta.1.gz|fasta.1|fna$|fna.gz|fna.1.gz|fna.1|fa$|fa.gz|fa.1.gz|fa.1|fastq$|fastq.gz|fastq.1.gz|fastq.1)")


####################################################
# Find ORFs using prodigal
####################################################
@follows(mkdir("orfs.dir"))
@transform(SEQUENCEFILES,SEQUENCEFILES_REGEX,r"orfs.dir/\1.orf_peptides")
def detectOrfs(infile,outfile):
    statementlist=[]
    #set job memory and threads
    job_memory = str(PARAMS["Prodigal_memory"])+"G"
    job_threads = int(PARAMS["Prodigal_threads"])
    #command to generate index files
    seqdat = PipelineAssembly.SequencingData(infile)
    #ensure input is FASTA
    if seqdat.paired == True:
        print("Prodigal requires single/merged (i.e. not paired-end) reads for ORF detection.")
    else:
        if seqdat.fileformat == "fastq":
            statementlist.append("reformat.sh in={} out={}".format(infile,"orfs.dir/"+seqdat.cleanname+".fa"))
            infile = "orfs.dir/"+seqdat.cleanname+".fa"
        #generate the call to prodigal
        statementlist.append(PipelineAnnotate.runProdigal(infile,outfile,PARAMS))
        #remove the temp FASTA if created
        if seqdat.fileformat == "fastq":
            statementlist.append("rm {}".format("orfs.dir/"+seqdat.cleanname+".fa"))
        statement = " && ".join(statementlist)
        P.run(statement)

######################################################################################################################
# Find seed orthologs of ORFs using eggnog-mapper with chunk splitting
######################################################################################################################
@follows(detectOrfs)
@follows(mkdir("functional_annotations.dir"))
@follows(mkdir("functional_annotations.dir/emapper_chunks"))
@subdivide(detectOrfs,regex(r"orfs.dir/(\S+).orf_peptides"),r"functional_annotations.dir/emapper_chunks/\1.*.chunk",r"functional_annotations.dir/emapper_chunks/\1")
def splitFasta(infile,outfiles,outfileroot):
    statement = "python {}/fastaToChunks.py --input {} --output_prefix {} --chunk_size {}".format(os.path.dirname(os.path.abspath(__file__)).replace("pipelines","scripts"),infile,os.getcwd()+"/"+outfileroot,PARAMS["Eggnogmapper_chunksize"])
    P.run(statement)

@follows(splitFasta)
@transform(splitFasta,regex(r"functional_annotations.dir/emapper_chunks/(\S+).chunk"),r"functional_annotations.dir/emapper_chunks/\1.chunk.emapper.seed_orthologs")
def functionalAnnotSeed(infile,outfile):
    job_memory = str(PARAMS["Eggnogmapper_memory"])+"G"
    job_threads = int(PARAMS["Eggnogmapper_threads"])
    #generate call to eggnog-mapper
    #requires older version of diamond to use the eggnog mapper databases
    statement = "module load bio/diamond/0.8.22 && "
    statement += PipelineAnnotate.runEggmapSeed(infile,infile,PARAMS)
    P.run(statement)

###############################################################
# Functional annotation of the seeds
###############################################################
@follows(functionalAnnotSeed)
@transform(functionalAnnotSeed,regex(r"functional_annotations.dir/emapper_chunks/(\S+).emapper.seed_orthologs"),r"functional_annotations.dir/emapper_chunks/\1.emapper.annotations")
def functionalAnnotChunks(infile,outfile):
    job_memory = str(PARAMS["Eggnogmapper_memory_annot"])+"G"
    job_threads = int(str(PARAMS["Eggnogmapper_threads_annot"]))
    #get annotation from seeds
    statement = PipelineAnnotate.runEggmapAnnot(infile,outfile.replace(".emapper.annotations",""),PARAMS)
    #run the annotation step
    P.run(statement)

###################################################
# Merge the functional annotations
##################################################
@follows(functionalAnnotChunks)
@collate(functionalAnnotChunks,regex(r"functional_annotations\.dir/emapper_chunks/(\S+)\.[0-9]+\.chunk\.emapper\.annotations"),r"functional_annotations.dir/\1.functional.annotations")
def functionalAnnot(infiles,outfile):
    statement = "cat {} >> {}".format(" ".join(infiles),outfile)
    P.run(statement)
    
##################################################
# Taxonomic alignment of ORFs using DIAMOND
#################################################
@follows(functionalAnnot)
@follows(mkdir("taxonomic_annotations.dir"))
@transform(detectOrfs,regex(r"orfs.dir/(\S+).orf_peptides"),r"taxonomic_annotations.dir/\1.daa")
def taxonomicAlign(infile,outfile):
    #set memory and threads
    job_memory = str(PARAMS["Diamond_memory"])+"G"
    job_threads = int(PARAMS["Diamond_threads"])
    #generate call to diamond
    statement = PipelineAnnotate.runDiamond(infile,outfile,PARAMS)
    P.run(statement)

########################################################################################################
# Get taxanomic annotation (and optionally kegg functions) from DIAMOND alignment using MEGAN blast2lca
########################################################################################################
@follows(taxonomicAlign)
@transform(taxonomicAlign,regex(r"taxonomic_annotations.dir/(\S+).daa"),r"taxonomic_annotations.dir/\1.taxonomic.annotations")
def meganAnnot(infile,outfile):
    job_memory = str(PARAMS["Blast2lca_memory"])+"G"
    job_threads = int(PARAMS["Blast2lca_threads"])
    #generate call to blast2lca
    statement = PipelineAnnotate.runBlast2Lca(infile,outfile,PARAMS)
    P.run(statement)


################################################
# Generate GTF summarising ORFs and annotations
################################################
@follows(meganAnnot)
@follows(mkdir("combined_annotations.dir"))
@transform(detectOrfs,regex(r"orfs.dir/(\S+).orf_peptides"),r"combined_annotations.dir/\1.orf_annotations.gtf")
def mergeAnnotations(infile,outfile):
    sampleName=re.search("orfs.dir/(\S+).orf_peptides",infile).group(1)
    func="functional_annotations.dir/{}.functional.annotations".format(sampleName)
    tax="taxonomic_annotations.dir/{}.taxonomic.annotations".format(sampleName)
    job_memory = str(PARAMS["Merge_memory"])+"G"
    statement = "python {}scripts/makeGtf.py --orfs {} --functions {} --taxonomy {} --output {} --output-short {}".format(
        os.path.dirname(__file__).rstrip("pipelines"), infile, func, tax, outfile, outfile.replace(".gtf","_short.gtf")
    )
    P.run(statement)

@follows(mergeAnnotations)
def full():
    pass


###################################################
# Summarise annotations
###################################################
@follows(mkdir("report.dir"))
@originate("report.dir/gtf_summary.tsv")
def summariseGTFs(outfile):
    job_memory = str(PARAMS["Merge_memory"])+"G"
    statement = "python {}scripts/annotationSummaryTable.py --gtf-dir combined_annotations.dir --annot-output {} --orf-output {}".format(
        os.path.dirname(__file__).rstrip("pipelines"),outfile,"report.dir/orf_summary.tsv")
    P.run(statement)

@follows(summariseGTFs)
def build_report():
    job_memory = str(PARAMS["Merge_memory"])+"G"
    scriptloc = "/".join(os.path.dirname(sys.argv[0]).split("/")[0:-1])+"/scripts/annotation_report.Rmd"
    statement = 'R -e "rmarkdown::render(\'{}\',output_file=\'{}/report.dir/annotation_report.html\')" --args {}/report.dir/gtf_summary.tsv {}/report.dir/orf_summary.tsv'.format(scriptloc,os.getcwd(),os.getcwd(),os.getcwd())
    P.run(statement)


    
if __name__ == "__main__":
    if sys.argv[1] == "plot":
        pipeline_printout_graph("test.pdf", "pdf", [full], no_key_legend=True,
                                size=(4, 4),
                                user_colour_scheme = {"colour_scheme_index": 1})
    else:
        sys.exit(P.main(sys.argv))



        
