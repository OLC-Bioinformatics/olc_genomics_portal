import os
import glob
import shutil
import datetime
import subprocess
from Bio import SeqIO, Seq
import multiprocessing
from io import StringIO
from django.conf import settings
from sentry_sdk import capture_exception
from itertools import product

from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions

import csv
from django.shortcuts import get_object_or_404

from celery import shared_task

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

from .models import PrimerVal

@shared_task
def run_primer_val(primer_request_pk):
    primer_request = PrimerVal.objects.get(pk = primer_request_pk)
    

    for record in SeqIO.parse(primer_request.primerfile, 'fasta'):
         degenerates = Seq.IUPAC. IUPACData.ambiguous_dna_values
         primerlist = list(map(''.join, product(*map(degenerates.get, str(record.seq).upper()))))
         primername = record.id
         for primer in primerlist:
            basename, direction = primername.split('-')
            if direction.startswith('F'):
                try:
                    self.forward_dict[basename].append(primer)
                except KeyError
                    self.forward_dict[basename] = list()
                    self.forward_dict[basename].append(primer)
            else:
                try:
                    self.reverse_dict[basename].append(primer)
                except KeyError:
                    self.reverse_dict[basename] = list()
                    self.reverse_dict[basename].append(primer)

def epcr_primer_file(self, formattedprimers):
    



