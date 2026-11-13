#!/usr/bin/env python3
"""Load Sequence Database records from the packaged spreadsheets."""

import os
import sys
from datetime import datetime

import django
import pandas

parentdir = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, parentdir)
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.prod'
django.setup()

from olc_webportalv2.sequence_database.models import (  # noqa: E402
    MLST,
    MLSTCC,
    RMLST,
    DatabaseRequest,
    DatabaseRequestIDs,
    GeneSeekr,
    Genus,
    LookupTable,
    NameTable,
    SequenceData,
    Serovar,
    Species,
    UniqueGeneSeekr,
    UniqueGenus,
    UniqueMLST,
    UniqueMLSTCC,
    UniqueRMLST,
    UniqueSerovar,
    UniqueSpecies,
    UniqueVtyper,
    Vtyper,
)

__author__ = 'adamkoziol'


class LoadDatabase(object):
    """Populate Sequence Database tables from metadata workbooks."""

    def main(self):
        """Read source workbooks, clear old data, and repopulate tables."""
        self.read_excel(
            spreadsheet=self.name_table,
            sheet='Sheet2',
            key='SEQID',
            dictionary=self.name_dictionary,
        )
        self.read_excel(
            spreadsheet=self.metadata_table,
            sheet='CFIAID',
            key='SEQID',
            dictionary=self.name_dictionary,
            name_only=True,
        )
        self.read_excel(
            spreadsheet=self.metadata_table,
            sheet='no CFIAID',
            key='SEQID',
            dictionary=self.name_dictionary,
            name_only=True,
        )
        self.read_excel(
            spreadsheet=self.metadata_table,
            sheet='CFIAID',
            key='SEQID',
            dictionary=self.sample_dictionary,
        )
        self.read_excel(
            spreadsheet=self.metadata_table,
            sheet='CFIAID',
            key='CFIAID',
            dictionary=self.cfid_id_dictionary,
        )
        self.read_excel(
            spreadsheet=self.metadata_table,
            sheet='no CFIAID',
            key='SEQID',
            dictionary=self.sample_dictionary,
        )
        self.read_excel(
            spreadsheet=self.metadata_table,
            sheet='no CFIAID',
            key='CFIAID',
            dictionary=self.cfid_id_dictionary,
        )
        self.create_type_list()
        # self.populate_lookup_table()
        # quit()
        self.clear_objects()
        self.populate_name_table()
        self.populate_tables()
        self.populate_unique_tables()

    @staticmethod
    def read_excel(spreadsheet, sheet, key, dictionary, name_only=False):
        """Read a workbook sheet into the provided identifier mapping.

        Args:
            spreadsheet: Path to the Excel workbook.
            sheet: Worksheet name to read.
            key: Column used as the dictionary key.
            dictionary: Mapping to update with the worksheet rows.
            name_only: Whether to load only identifier and curation fields.
        """
        print(f'Reading {spreadsheet}')
        data_dict = (
            pandas.read_excel(spreadsheet, sheet)
            .fillna('ND')
            .set_index(key)
            .T.to_dict()
        )
        for identifier, nested_dict in data_dict.items():
            identifier = str(identifier)
            if name_only:
                if identifier not in dictionary:
                    dictionary[identifier] = {
                        'SEQID': identifier,
                        'CFIAID': str(nested_dict['CFIAID']),
                        'CuratorFlag': 'METADATA_REFERENCE',
                    }
            else:
                if identifier not in dictionary:
                    dictionary[identifier] = {key: identifier}
                for header, value in nested_dict.items():
                    if type(value) is float:
                        value = int(value)
                    if header == 'AssemblyDate':
                        if value == 'ND':
                            value = datetime.strptime(
                                '-'.join(['1900', '1', '1']), '%Y-%m-%d'
                            )
                        else:
                            value = value.date()
                        dictionary[identifier].update({header: value})
                    else:
                        dictionary[identifier].update({header: str(value)})

    def find_strain_data(self, seqid):
        """Find metadata for a sequence or its associated CFIA ID.

        Args:
            seqid: The sequence identifier to look up.
        """
        try:
            strain_dict = self.sample_dictionary[seqid]
        except KeyError:
            try:
                strain_dict = self.cfid_id_dictionary[
                    self.name_dictionary[seqid]['CFIAID']
                ]
            # Missing CFIA metadata means no strain attributes are available.
            except KeyError:
                strain_dict = dict()
        return strain_dict

    def create_type_list(self):
        """Identify representative sequences and related aliases."""
        type_set = set()
        for seqid, data_dict in sorted(self.sample_dictionary.items()):
            try:
                self.type_dictionary[data_dict['CFIAID']] = {
                    'SEQID': str(),
                    'OTHERS': list(),
                }
                type_set.add(seqid)
            except KeyError:
                pass
        for seqid in sorted(self.name_dictionary):
            strain_dict = self.find_strain_data(seqid=seqid)
            try:
                type_id = strain_dict['SEQID']
            except KeyError:
                type_id = str()
            cfiaid = self.name_dictionary[seqid]['CFIAID']
            if type_id:
                try:
                    if seqid in type_set:
                        self.type_dictionary[cfiaid]['SEQID'] = seqid
                    else:
                        self.type_dictionary[cfiaid]['OTHERS'].append(seqid)
                except KeyError:
                    print('keyerr', seqid, type_id, cfiaid, strain_dict)
                    if cfiaid not in self.fail_dictionary:
                        self.fail_dictionary[cfiaid] = list()
                    self.fail_dictionary[cfiaid].append(seqid)

            else:
                print('no type', seqid, type_id, strain_dict)
                if cfiaid not in self.fail_dictionary:
                    self.fail_dictionary[cfiaid] = list()
                self.fail_dictionary[cfiaid].append(seqid)

    def populate_name_table(self):
        """Create name and alias lookup records."""
        print('Populating name table')
        for seqid, data_dict in sorted(self.name_dictionary.items()):
            cfiaid = data_dict['CFIAID']

            NameTable.objects.get_or_create(
                seqid=seqid,
                cfiaid=cfiaid,
                curatorflag=self.name_dictionary[seqid]['CuratorFlag'],
            )
            try:
                gold_standard = self.type_dictionary[cfiaid]['SEQID']
                LookupTable.objects.get_or_create(
                    cfiaid=NameTable.objects.get(cfiaid=cfiaid, seqid=seqid),
                    seqid=gold_standard,
                    olnid=self.sample_dictionary[gold_standard]['OLNID'],
                    labid=self.sample_dictionary[gold_standard]['LabID'],
                    biosample=self.sample_dictionary[gold_standard][
                        'BIOSAMPLE'
                    ],
                    other_names=self.type_dictionary[cfiaid]['OTHERS'],
                )
            except KeyError:
                print('no gold!', cfiaid, self.fail_dictionary[cfiaid])
                LookupTable.objects.get_or_create(
                    cfiaid=NameTable.objects.get(cfiaid=cfiaid, seqid=seqid),
                    seqid='',
                    olnid='',
                    labid='',
                    biosample='',
                    other_names=[],
                )

    def populate_tables(self):
        """Populate sequence metadata and collect unique filter values."""
        print('Lots of tables')
        for cfiaid, data_dict in sorted(self.type_dictionary.items()):
            # if data_dict:
            seqid = data_dict['SEQID']
            if seqid:
                typing_date = self.sample_dictionary[seqid]['AssemblyDate']
                version = self.sample_dictionary[seqid]['PipelineVersion']
                self.unique_genera.add(self.sample_dictionary[seqid]['Genus'])
                self.unique_species.add(
                    self.sample_dictionary[seqid]['Species']
                )
                self.unique_mlst.add(self.sample_dictionary[seqid]['MLST'])
                self.unique_mlstcc.add(
                    self.sample_dictionary[seqid]['MLST-CC']
                )
                self.unique_rmlst.add(self.sample_dictionary[seqid]['rMLST'])
                self.unique_geneseekr.add(
                    self.sample_dictionary[seqid]['GeneSeekr_Profile']
                )
                self.unique_serovar.add(
                    self.sample_dictionary[seqid]['Serovar']
                )
                self.unique_vtyper.add(
                    self.sample_dictionary[seqid]['Vtyper_Profile']
                )
                Genus.objects.get_or_create(
                    seqid=seqid, genus=self.sample_dictionary[seqid]['Genus']
                )
                Species.objects.get_or_create(
                    seqid=seqid,
                    species=self.sample_dictionary[seqid]['Species'],
                )
                MLST.objects.get_or_create(
                    seqid=seqid,
                    mlst=self.sample_dictionary[seqid]['MLST'],
                    version=version,
                    typing_date=typing_date,
                )
                MLSTCC.objects.get_or_create(
                    seqid=seqid,
                    mlst_cc=self.sample_dictionary[seqid]['MLST-CC'],
                    version=version,
                    typing_date=typing_date,
                )
                RMLST.objects.get_or_create(
                    seqid=seqid,
                    rmlst=self.sample_dictionary[seqid]['rMLST'],
                    version=version,
                    typing_date=typing_date,
                )
                GeneSeekr.objects.get_or_create(
                    seqid=seqid,
                    geneseekr=self.sample_dictionary[seqid][
                        'GeneSeekr_Profile'
                    ],
                    version=version,
                    typing_date=typing_date,
                )
                Serovar.objects.get_or_create(
                    seqid=seqid,
                    serovar=self.sample_dictionary[seqid]['Serovar'],
                    version=version,
                    typing_date=typing_date,
                )
                Vtyper.objects.get_or_create(
                    seqid=seqid,
                    vtyper=self.sample_dictionary[seqid]['Vtyper_Profile'],
                    version=version,
                    typing_date=typing_date,
                )
                SequenceData.objects.get_or_create(
                    seqid=seqid,
                    cfiaid=NameTable.objects.get(seqid=seqid),
                    genus=Genus.objects.get(seqid=seqid),
                    species=Species.objects.get(seqid=seqid),
                    mlst=MLST.objects.get(seqid=seqid),
                    mlst_cc=MLSTCC.objects.get(seqid=seqid),
                    rmlst=RMLST.objects.get(seqid=seqid),
                    geneseekr=GeneSeekr.objects.get(seqid=seqid),
                    serovar=Serovar.objects.get(seqid=seqid),
                    vtyper=Vtyper.objects.get(seqid=seqid),
                    version=version,
                    typing_date=typing_date,
                )
            else:
                print('no seqid', cfiaid, data_dict)

    def populate_unique_tables(self):
        """Create autocomplete records for each distinct metadata value."""
        print('MORE tables!')
        for genus in sorted(list(self.unique_genera)):
            UniqueGenus.objects.get_or_create(genus=genus)
        for species in sorted(list(self.unique_species)):
            UniqueSpecies.objects.get_or_create(species=species)
        for mlst in sorted(list(self.unique_mlst)):
            UniqueMLST.objects.get_or_create(mlst=mlst)
        for mlstcc in sorted(list(self.unique_mlstcc)):
            UniqueMLSTCC.objects.get_or_create(mlst_cc=mlstcc)
        for rmlst in sorted(list(self.unique_rmlst)):
            UniqueRMLST.objects.get_or_create(rmlst=rmlst)
        for geneseekr in sorted(list(self.unique_geneseekr)):
            UniqueGeneSeekr.objects.get_or_create(geneseekr=geneseekr)
        for serovar in sorted(list(self.unique_serovar)):
            UniqueSerovar.objects.get_or_create(serovar=serovar)
        for vtyper in sorted(list(self.unique_vtyper)):
            UniqueVtyper.objects.get_or_create(vtyper=vtyper)

    @staticmethod
    def clear_objects():
        """Remove loaded sequence metadata before rebuilding it."""
        Genus.objects.all().delete()
        Species.objects.all().delete()
        MLST.objects.all().delete()
        MLSTCC.objects.all().delete()
        RMLST.objects.all().delete()
        GeneSeekr.objects.all().delete()
        Serovar.objects.all().delete()
        Vtyper.objects.all().delete()
        LookupTable.objects.all().delete()
        NameTable.objects.all().delete()
        SequenceData.objects.all().delete()
        DatabaseRequest.objects.all().delete()
        DatabaseRequestIDs.objects.all().delete()
        UniqueGenus.objects.all().delete()
        UniqueSpecies.objects.all().delete()
        UniqueMLST.objects.all().delete()
        UniqueMLSTCC.objects.all().delete()
        UniqueRMLST.objects.all().delete()
        UniqueGeneSeekr.objects.all().delete()
        UniqueSerovar.objects.all().delete()
        UniqueVtyper.objects.all().delete()

    def __init__(self):
        """Initialize workbook paths and in-memory lookup collections."""
        self.homepath = os.path.split(os.path.abspath(__file__))[0]
        self.name_table = os.path.join(self.homepath, 'name_table.xlsx')
        self.metadata_table = os.path.join(
            self.homepath, 'metadata_table.xlsx'
        )
        self.name_dictionary = dict()
        self.sample_dictionary = dict()
        self.cfid_id_dictionary = dict()
        self.type_dictionary = dict()
        self.fail_dictionary = dict()
        self.gold_standard_dictionary = dict()
        self.unique_genera = set()
        self.unique_species = set()
        self.unique_mlst = set()
        self.unique_mlstcc = set()
        self.unique_rmlst = set()
        self.unique_geneseekr = set()
        self.unique_serovar = set()
        self.unique_vtyper = set()


def cli():
    """Run the database loading workflow."""
    loader = LoadDatabase()
    loader.main()


if __name__ == '__main__':
    cli()
