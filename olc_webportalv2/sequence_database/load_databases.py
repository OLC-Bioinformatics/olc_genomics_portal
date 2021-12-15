#!/usr/bin/env python3
from datetime import datetime
import django
import pandas
import os

parentdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.sys.path.insert(0, parentdir)
print(parentdir)
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.prod'
django.setup()
from olc_webportalv2.sequence_database.models import GeneSeekr, Genus, LookupTable, NameTable, MLST, MLSTCC, RMLST, \
    SequenceData, Serovar, Species, Vtyper, UniqueGenus, UniqueSpecies, UniqueMLST, UniqueMLSTCC, UniqueRMLST, \
    UniqueGeneSeekr, UniqueSerovar, UniqueVtyper, DatabaseRequest, DatabaseRequestIDs

__author__ = 'adamkoziol'


class LoadDatabase(object):

    def main(self):
        # self.populate_name_table()
        # self.populate_sequence_data_table()
        # self.clear_objects()
        # quit()
        # Read in the name table
        self.read_excel(spreadsheet=self.name_table,
                        sheet='Sheet2',
                        key='SEQID',
                        dictionary=self.name_dictionary)
        self.read_excel(spreadsheet=self.metadata_table,
                        sheet='CFIAID',
                        key='SEQID',
                        dictionary=self.name_dictionary,
                        name_only=True)
        self.read_excel(spreadsheet=self.metadata_table,
                        sheet='no CFIAID',
                        key='SEQID',
                        dictionary=self.name_dictionary,
                        name_only=True)
        self.read_excel(spreadsheet=self.metadata_table,
                        sheet='CFIAID',
                        key='SEQID',
                        dictionary=self.sample_dictionary)
        self.read_excel(spreadsheet=self.metadata_table,
                        sheet='CFIAID',
                        key='CFIAID',
                        dictionary=self.cfid_id_dictionary)
        self.read_excel(spreadsheet=self.metadata_table,
                        sheet='no CFIAID',
                        key='SEQID',
                        dictionary=self.sample_dictionary)
        self.read_excel(spreadsheet=self.metadata_table,
                        sheet='no CFIAID',
                        key='CFIAID',
                        dictionary=self.cfid_id_dictionary)
        self.create_type_list()
        # self.populate_lookup_table()
        # quit()
        self.clear_objects()
        self.populate_name_table()
        self.populate_tables()
        self.populate_unique_tables()
        quit()
        # count = 0
        # listo = [sid for sid in sorted(self.name_dictionary)]
        # listo.insert(0, '2013-SEQ-0002')
        # for seqid in listo:
        #     strain_dict = self.find_strain_data(seqid=seqid)
        #     for key, value in strain_dict.items():
        #         print(seqid, key, value)
        #
        #     count += 1
        #     if count > 3:
        #         break

        # for sample, sample_dict in sorted(self.sample_dictionary.items()):
        #     print(sample, sample_dict)
        #     break
        # try:
        #     print('2013-SEQ-0132')
        #     print(self.name_dictionary['2013-SEQ-0132'])
        #     print(self.sample_dictionary['2013-SEQ-0132'])
        # except KeyError:
        #     print(self.cfid_id_dictionary[self.name_dictionary['2013-SEQ-0132']['CFIAID']])

    @staticmethod
    def read_excel(spreadsheet, sheet, key, dictionary, name_only=False):
        """

        :param spreadsheet:
        :param sheet:
        :param key:
        :param dictionary:
        :param name_only:
        """
        print('Reading {ss}'.format(ss=spreadsheet))
        data_dict = pandas.read_excel(spreadsheet, sheet).fillna('ND').set_index(key).T.to_dict()
        for identifier, nested_dict in data_dict.items():
            identifier = str(identifier)
            if name_only:
                if identifier not in dictionary:
                    dictionary[identifier] = {
                        'SEQID': identifier,
                        'CFIAID': str(nested_dict['CFIAID']),
                        'CuratorFlag': 'METADATA_REFERENCE'
                    }
            else:
                if identifier not in dictionary:
                    dictionary[identifier] = {key: identifier}
                for header, value in nested_dict.items():
                    if type(value) is float:
                        value = int(value)
                    if header == 'AssemblyDate':
                        if value == 'ND':
                            value = datetime.strptime("-".join(['1900', '1', '1']), '%Y-%m-%d')
                        else:
                            value = value.date()
                        dictionary[identifier].update({header: value})
                    else:
                        dictionary[identifier].update({header: str(value)})

    def find_strain_data(self, seqid):
        try:
            strain_dict = self.sample_dictionary[seqid]
        except KeyError:
            try:
                strain_dict = self.cfid_id_dictionary[self.name_dictionary[seqid]['CFIAID']]
            # If there are no metadata associated with a CFIAID (no successful assemblies), create an empty dictionary
            except KeyError:
                strain_dict = dict()
        return strain_dict

    def create_type_list(self):
        type_set = set()
        for seqid, data_dict in sorted(self.sample_dictionary.items()):

            try:
                self.type_dictionary[data_dict['CFIAID']] = {
                    'SEQID': str(),
                    'OTHERS': list()
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
                    # self.type_dictionary[cfiaid]['OTHERS'] = list()
                    #     self.type_dictionary[cfiaid]['SEQID'] = str()
                    if seqid in type_set:
                        self.type_dictionary[cfiaid]['SEQID'] = seqid
                        # self.gold_standard_dictionary[seqid] = ''
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
                # print('fail', seqid, cfiaid, self.fail_dictionary[cfiaid], self.name_dictionary[seqid]['CuratorFlag'])

    def populate_name_table(self):
        print('Populating name table')
        for seqid, data_dict in sorted(self.name_dictionary.items()):
            cfiaid = data_dict['CFIAID']

            NameTable.objects.get_or_create(seqid=seqid,
                                            cfiaid=cfiaid,
                                            curatorflag=self.name_dictionary[seqid]['CuratorFlag'])
            try:
                gold_standard = self.type_dictionary[cfiaid]['SEQID']
                LookupTable.objects.get_or_create(cfiaid=NameTable.objects.get(cfiaid=cfiaid, seqid=seqid),
                                                  seqid=gold_standard,
                                                  olnid=self.sample_dictionary[gold_standard]['OLNID'],
                                                  labid=self.sample_dictionary[gold_standard]['LabID'],
                                                  biosample=self.sample_dictionary[gold_standard]['BIOSAMPLE'],
                                                  other_names=self.type_dictionary[cfiaid]['OTHERS'])
            except KeyError:
                print('no gold!', cfiaid, self.fail_dictionary[cfiaid])
                LookupTable.objects.get_or_create(cfiaid=NameTable.objects.get(cfiaid=cfiaid, seqid=seqid),
                                                  seqid='',
                                                  olnid='',
                                                  labid='',
                                                  biosample='',
                                                  other_names=[])

    def populate_tables(self):
        print('Lots of tables')
        for cfiaid, data_dict in sorted(self.type_dictionary.items()):
            # if data_dict:
            seqid = data_dict['SEQID']
            if seqid:
                typing_date = self.sample_dictionary[seqid]['AssemblyDate']
                version = self.sample_dictionary[seqid]['PipelineVersion']
                self.unique_genera.add(self.sample_dictionary[seqid]['Genus'])
                self.unique_species.add(self.sample_dictionary[seqid]['Species'])
                self.unique_mlst.add(self.sample_dictionary[seqid]['MLST'])
                self.unique_mlstcc.add(self.sample_dictionary[seqid]['MLST-CC'])
                self.unique_rmlst.add(self.sample_dictionary[seqid]['rMLST'])
                self.unique_geneseekr.add(self.sample_dictionary[seqid]['GeneSeekr_Profile'])
                self.unique_serovar.add(self.sample_dictionary[seqid]['Serovar'])
                self.unique_vtyper.add(self.sample_dictionary[seqid]['Vtyper_Profile'])
                Genus.objects.get_or_create(seqid=seqid,
                                            genus=self.sample_dictionary[seqid]['Genus'])
                Species.objects.get_or_create(seqid=seqid,
                                              species=self.sample_dictionary[seqid]['Species'])
                MLST.objects.get_or_create(seqid=seqid,
                                           mlst=self.sample_dictionary[seqid]['MLST'],
                                           version=version,
                                           typing_date=typing_date)
                MLSTCC.objects.get_or_create(seqid=seqid,
                                             mlst_cc=self.sample_dictionary[seqid]['MLST-CC'],
                                             version=version,
                                             typing_date=typing_date)
                RMLST.objects.get_or_create(seqid=seqid,
                                            rmlst=self.sample_dictionary[seqid]['rMLST'],
                                            version=version,
                                            typing_date=typing_date)
                GeneSeekr.objects.get_or_create(seqid=seqid,
                                                geneseekr=self.sample_dictionary[seqid]['GeneSeekr_Profile'],
                                                version=version,
                                                typing_date=typing_date)
                Serovar.objects.get_or_create(seqid=seqid,
                                              serovar=self.sample_dictionary[seqid]['Serovar'],
                                              version=version,
                                              typing_date=typing_date)
                Vtyper.objects.get_or_create(seqid=seqid,
                                             vtyper=self.sample_dictionary[seqid]['Vtyper_Profile'],
                                             version=version,
                                             typing_date=typing_date)
                SequenceData.objects.get_or_create(seqid=seqid,
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
                                                   typing_date=typing_date)
            else:
                print('no seqid', cfiaid, data_dict)

    def populate_unique_tables(self):
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

    # @staticmethod
    # def populate_sequence_data_table():
    #     try:
    #         SequenceData.objects.all().delete()
    #     except:
    #         pass
    #     SequenceData.objects.get_or_create(seqid='2013-SEQ-0045',
    #                                        cfiaid=NameTable.objects.get(seqid='2013-SEQ-0045'),
    #                                        genus=Genus.objects.get(seqid='2013-SEQ-0045'))
    # rmlst='',
    # mlst='',
    # mlst_cc='',
    # genus='',
    # species='',
    # serovar='',
    # geneseekr='',
    # vtyper='')
    # rmlst = models.ForeignKey(RMLST, on_delete=models.CASCADE, null=True)  # Same as MLST. Numeric, but categorical.
    # mlst = models.ForeignKey(MLST, on_delete=models.CASCADE, null=True)  # MLST is numeric, but categorical, so keep as CharField
    # mlst_cc = models.ForeignKey(MLSTCC, on_delete=models.CASCADE, null=True)  # MLST is numeric, but categorical, so keep as CharField
    #
    # genus = models.ForeignKey(Genus, on_delete=models.CASCADE, null=True)
    # species = models.ForeignKey(Species, on_delete=models.CASCADE, null=True)
    # serovar = models.ForeignKey(Serovar, on_delete=models.CASCADE, null=True)
    # geneseekr = models.ForeignKey(Geneseekr, on_delete=models.CASCADE, null=True)
    # vtyper = models.ForeignKey(Vtyper, on_delete=models.CASCADE, null=True))

    @staticmethod
    def clear_objects():
        try:
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
        except Exception as e:
            print(e)

    def __init__(self):
        # Extract the path of the current script from the full path + file name
        self.homepath = os.path.split(os.path.abspath(__file__))[0]
        self.name_table = os.path.join(self.homepath, 'name_table.xlsx')
        self.metadata_table = os.path.join(self.homepath, 'metadata_table.xlsx')
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
    loader = LoadDatabase()
    loader.main()


if __name__ == '__main__':
    cli()
