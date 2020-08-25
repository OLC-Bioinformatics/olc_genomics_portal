import os, xlrd
from olctools.accessoryFunctions.accessoryFunctions import relative_symlink

thisdir = '/home/b/Downloads/sequence_database_fastas'
metadata = "/home/b/metadata_table.xlsx"
sal = "/home/b/salmonella"
lis = "/home/b/listeria"
cam = "/home/b/campy"
ecoli = "/home/b/ecoli"

# os.mkdir(sal)
# os.mkdir(lis)
# os.mkdir(cam)
# os.mkdir(ecoli)

# a = (len(os.listdir(sal)))
# b = (len(os.listdir(lis)))
# c = (len(os.listdir(cam)))
# d = (len(os.listdir(ecoli)))

# total= a + b + c+d
# print(total)
# print(len(os.listdir(thisdir)))


# for x in os.listdir(sal):
#         if os.path.islink(sal+"/"+x):
#                 print("True - "+x)

# for x in os.listdir(lis):
#         if os.path.islink(lis+"/"+x):
#                 print("True - "+x)

# for x in os.listdir(cam):
#         if os.path.islink(cam+"/"+x):
#                 print("True - "+x)

# for x in os.listdir(ecoli):
#         if os.path.islink(ecoli+"/"+x):
#                 print("True - "+x)



# wb = xlrd.open_workbook(metadata)
# sheet = wb.sheet_by_index(0)
# sheet2 = wb.sheet_by_index(1)
# print(sheet.nrows)

# print(sheet2.nrows)

# print(sheet.nrows + sheet2.nrows)

# for x in os.listdir(thisdir):
#     seq = os.path.splitext(x)[0]
#     for r in range(sheet.nrows):
#         row = sheet.row(r)
#         if (row[0].value == seq):
#             if(row[7].value == "Salmonella"):
#                 relative_symlink(thisdir+"/"+x, sal)
#             elif(row[7].value == "Listeria"):
#                 relative_symlink(thisdir+"/"+x, lis)
#             elif(row[7].value == "Campylobacter"):
#                 relative_symlink(thisdir+"/"+x, cam)
#             elif(row[7].value == "Escherichia"):
#                 relative_symlink(thisdir+"/"+x, ecoli)
#             else:
#                 pass
#             print(row[7].value)
        

# for x in os.listdir(thisdir):
#     seq = os.path.splitext(x)[0]
#     for r in range(sheet2.nrows):
#         row = sheet2.row(r)
#         if (row[0].value == seq):
#             if(row[7].value == "Salmonella"):
#                 relative_symlink(thisdir+"/"+x, sal)
#             elif(row[7].value == "Listeria"):
#                 relative_symlink(thisdir+"/"+x, lis)
#             elif(row[7].value == "Campylobacter"):
#                 relative_symlink(thisdir+"/"+x, cam)
#             elif(row[7].value == "Escherichia"):
#                 relative_symlink(thisdir+"/"+x, ecoli)
#             else:
#                 pass
#             print(row[7].value)
                
#         if sheet.cell(row,1).value == seq:
#             print(seq + row)'Downloads/sequence_database_fastas'


# os.system('{famap} -b {outfile}.famap {fasta}'.format(famap='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/famap',outfile=sal,fasta=os.path.join('/home/b/salmonella', '*.fasta')))
# os.system('{famap} -b {outfile}.famap {fasta}'.format(famap='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/famap',outfile=lis,fasta=os.path.join('/home/b/listeria', '*.fasta')))
# os.system('{famap} -b {outfile}.famap {fasta}'.format(famap='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/famap',outfile=ecoli,fasta= os.path.join('/home/b/ecoli', '*.fasta')))
# os.system('{famap} -b {outfile}.famap {fasta}'.format(famap='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/famap',outfile=cam,fasta=os.path.join('/home/b/campy', '*.fasta')))
# os.system('{famap} -b {outfile}.famap {fasta}'.format(famap='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/famap',outfile="all",fasta=os.path.join('/home/b/Downloads/sequence_database_fastas', '*.fasta')))

# os.system('{fahash} -b {outfile}.hash {outfile}.famap'.format(fahash='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/fahash',outfile='/home/b/salmonella'))
# os.system('{fahash} -b {outfile}.hash {outfile}.famap'.format(fahash='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/fahash',outfile='/home/b/listeria'))
# os.system('{fahash} -b {outfile}.hash {outfile}.famap'.format(fahash='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/fahash',outfile='/home/b/ecoli'))
# os.system('{fahash} -b {outfile}.hash {outfile}.famap'.format(fahash='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/fahash',outfile='/home/b/campy'))
# os.system('{fahash} -b {file}.hash {out}.famap'.format(fahash='/home/b/miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/fahash',file="/home/b/all",out='/media/b/External/CFIA/all'))


# primers = 'vtx_subtyping_primers.txt'
# os.system('{rePCR} -S {outfile}.hash -r + -m {ampsize} -n {mismatches} -g 0 -G -q -o {outfile}.txt {primers}'.format(rePCR='./miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/re-PCR',
#                             outfile='/media/b/External/CFIA/ecoli',
#                             ampsize='10000',
#                             mismatches='1',
#                             primers=primers))

# sprimers = 'salprimers.txt'
# os.system('{rePCR} -S {out}.hash -r + -m {ampsize} -n {mismatches} -g 0 -G -q -o {outfile}.txt {primers}'.format(rePCR='./miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/re-PCR',
#                             outfile='/home/b/sal',
#                             out='/media/b/External/CFIA/salmonella',
#                             ampsize='10000',
#                             mismatches='1',
#                             primers=sprimers))

# lprimers = 'lisprimers.txt'
# os.system('{rePCR} -S {out}.hash -r + -m {ampsize} -n {mismatches} -g 0 -G -q -o {outfile}.txt {primers}'.format(rePCR='./miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/re-PCR',
#                             outfile='/home/b/lis',
#                             out='/media/b/External/CFIA/listeria',
#                             ampsize='10000',
#                             mismatches='1',
#                             primers=lprimers))

# cprimers = 'camprimers.txt'
# os.system('{rePCR} -S {out}.hash -r + -m {ampsize} -n {mismatches} -g 0 -G -q -o {outfile}.txt {primers}'.format(rePCR='./miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/re-PCR',
#                             outfile='/home/b/cam',
#                             out='/media/b/External/CFIA/campy',
#                             ampsize='10000',
#                             mismatches='1',
#                             primers=cprimers))
