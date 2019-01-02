#!/usr/bin/env python

import csv

with open('SeqTracking.csv', encoding='ISO-8859-1') as csvfile:
    with open('Metadata_csv.csv', 'w') as outfile:
        outfile.write('SeqID,Genus,Quality\n')
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['Genus'] == '':
                genus = 'NA'
            else:
                genus = row['Genus']
            seqid = row['SEQID']
            if row['CuratorFlag'] == 'REFERENCE':
                    quality = 'Reference'
            elif row['CuratorFlag'] == 'PASS':
                    quality = 'Pass'
            else:
                    quality = 'Fail'
            outfile.write('{},{},{}\n'.format(seqid, genus, quality))

