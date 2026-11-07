from django.test import TestCase, RequestFactory
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from unittest import mock
import json
import xml.etree.ElementTree as ElementTree
import zipfile

from olc_webportalv2.primer_finder.forms import (
	ValidatorExperimentForm,
	ValidatorTargetForm,
	ValidatorTargetFormSetFactory,
	VerifierForm,
)
from olc_webportalv2.primer_finder.models import (
	ValidatorAnalysisTask,
	ValidatorAzureRequest,
	VerifierRequest,
	VerifierAnalysisTask,
	VerifierPrimerSet,
	ValidatorRequest,
	ValidatorPrimerSet,
	ValidatorSample,
	ValidatorTarget,
	ValidatorPanel,
		ValidatorPrimers,
	VerifierPanel,
	VerifierPrimers,
)
from olc_webportalv2.metadata.models import LabID
from olc_webportalv2.primer_finder.methods import (
	_archive_reports_and_upload,
	_upload_summary_excel,
	_validator_classification_label,
	_validator_logic_label,
	_validator_probe_records,
	categorize_gene_hits,
	categorize_gene_hit_details,
	filter_report_by_gene_categories,
	upload_probe,
)
from olc_webportalv2.metadata.models import Genus, SequenceData
from olc_webportalv2.primer_finder.tasks import (
	_format_task_error,
	_record_request_error,
	_check_tasks_completed,
	_prepare_validator_analysis,
	_process_validator_analysis_task,
	_process_validator_task,
	check_validator_tasks,
	_handle_successful_validator_run,
	_handle_failed_validator_run,
	run_primer_validator,
	_create_validator_gene_task_command,
	_upload_validator_gene_targets,
	_normalize_gene_reports,
	_validator_tasks,
	_check_exit_codes,
	_finalize_validator_request,
	_handle_panel_files,
	_create_focused_primer_command,
	_create_focused_geneseekr_command,
	_delete_batch_resources,
	_submit_validator_task,
	_submit_verifier_task,
	_verifier_tasks,
)
from olc_webportalv2.primer_finder import tasks as primer_tasks
from olc_webportalv2.primer_finder import views as primer_views
from olc_webportalv2.primer_finder.templatetags.dict_get import dict_get
from olc_webportalv2.primer_finder.templatetags.display_filters import display_name
from olc_webportalv2.users.models import User


class ValidatorConsolidatedFormTests(TestCase):
	"""Cover validation for the consolidated locked-down Validator input."""

	def test_report_archive_skips_virtual_folder_markers(self):
		blob_client = mock.Mock()
		folder_blob = mock.Mock()
		folder_blob.name = 'reports/gene'
		report_blob = mock.Mock()
		report_blob.name = 'reports/gene/report.json'
		blob_client.list_blobs.return_value = [folder_blob, report_blob]

		_archive_reports_and_upload(
			blob_client=blob_client,
			container_name='primer-validator-155',
		)

		downloaded_names = [
			call[1]['blob_name']
			for call in blob_client.get_blob_to_path.call_args_list
		]
		self.assertEqual(downloaded_names, ['reports/gene/report.json'])

	def test_summary_workbook_includes_strain_metadata_and_result(self):
		user = User.objects.create_user(username='summary-metadata-test')
		request = VerifierRequest.objects.create(
			user=user,
			inclusivity_panel=['bds-salmonella'],
			primer_sequences='>ST11_15-F\nACGT\n>ST11_15-R\nTGCA\n',
			report=json.dumps({
				'ST11_15': {
					'inclusivity': {
						'2025-SEQ-0789': {
							'amplicon_length': '429',
							'total_mismatch': '0',
						}
					}
				}
			}),
		)
		primer_set = VerifierPrimerSet.objects.create(
			verifier_request=request, primer_name='ST11_15'
		)
		VerifierPrimers.objects.create(
			primer=primer_set, primer_header='ST11_15-F',
			primer_sequence='ACGT',
		)
		VerifierPrimers.objects.create(
			primer=primer_set, primer_header='ST11_15-R',
			primer_sequence='TGCA',
		)
		VerifierPanel.objects.create(
			verifier_request=request, genus='bds-salmonella',
			panel='inclusivity',
		)
		labid = LabID.objects.create(labid='BDS-Salm001')
		SequenceData.objects.create(
			seqid='2025-SEQ-0789', quality='Pass', genus='Salmonella',
			species='Salmonella enterica', subspecies='enterica',
			serotype='Sundsvall', labid=labid,
		)

		workbook_values = {}
		blob_client = mock.Mock()

		def read_workbook(**kwargs):
			with zipfile.ZipFile(kwargs['file_path']) as workbook:
				names = workbook.namelist()
				shared_strings = []
				if 'xl/sharedStrings.xml' in names:
					root = ElementTree.fromstring(
						workbook.read('xl/sharedStrings.xml')
					)
					shared_strings = [
						''.join(node.itertext())
						for node in root
					]
				root = ElementTree.fromstring(workbook.read('xl/worksheets/sheet1.xml'))
				rows = []
				for row in root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'):
					values = []
					for cell in row:
						value = cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
						text = value.text if value is not None else ''
						if cell.get('t') == 's':
							text = shared_strings[int(text)]
						values.append(text)
					rows.append(values)
				workbook_values['rows'] = rows

		blob_client.create_blob_from_path.side_effect = read_workbook
		_upload_summary_excel(
			blob_client=blob_client,
			container_name='primer-verifier-test',
			request=request,
		)
		self.assertEqual(
			workbook_values['rows'][0][:8],
			['IsolateID', 'SEQID', 'Species', 'Subspecies', 'Serovar',
			 'PANEL', 'BENCHMARK PANEL', 'RESULT'],
		)
		self.assertEqual(
			workbook_values['rows'][1][:8],
			['BDS-Salm001', '2025-SEQ-0789', 'Salmonella enterica',
			 'enterica', 'Sundsvall', 'INCLUSIVITY', 'BDS-Salmonella',
			 'Salmonella'],
		)
		self.assertNotIn('LOGIC', workbook_values['rows'][0])
		self.assertNotIn('Matched Exclusion Categories', workbook_values['rows'][0])
		self.assertNotIn('Gene category details', ' '.join(workbook_values['rows'][0]))

	def test_validator_home_downloads_report_archive(self):
		user = User.objects.create_user(username='validator-download-test')
		ValidatorRequest.objects.create(
			user=user,
			project_name='completed validator',
			forward_primer='ACGT',
			reverse_primer='TGCA',
			status='Complete',
			report_download_link='https://storage.example/reports.zip',
			summary_download_link='https://storage.example/summary.xlsx',
		)
		self.client.force_login(user)

		response = self.client.get(
			reverse('primer_finder:primer_validator_home')
		)

		self.assertContains(response, 'https://storage.example/reports.zip')
		self.assertNotContains(response, 'https://storage.example/summary.xlsx')

	def test_summary_workbook_uses_panel_metadata_exports(self):
		user = User.objects.create_user(username='csv-summary-metadata-test')
		request = VerifierRequest.objects.create(
			user=user,
			inclusivity_panel=['bds-salmonella'],
			exclusivity_panel=['bds-exclusivity'],
			report=json.dumps({
				'ST11_15': {
					'inclusivity': {
						'2025-SEQ-0789': {'amplicon_length': '429'},
						'ERR11874512': {'amplicon_length': '429'},
					},
					'exclusivity': {
						'2025-SEQ-0996': {'amplicon_length': 'ND'},
					},
				},
			}),
		)
		primer_set = VerifierPrimerSet.objects.create(
			verifier_request=request, primer_name='ST11_15'
		)
		VerifierPrimers.objects.create(
			primer=primer_set, primer_header='ST11_15-F',
			primer_sequence='ACGT',
		)
		VerifierPrimers.objects.create(
			primer=primer_set, primer_header='ST11_15-R',
			primer_sequence='TGCA',
		)

		workbook_values = {}
		blob_client = mock.Mock()

		def read_workbook(**kwargs):
			with zipfile.ZipFile(kwargs['file_path']) as workbook:
				shared_strings = []
				if 'xl/sharedStrings.xml' in workbook.namelist():
					shared_root = ElementTree.fromstring(
						workbook.read('xl/sharedStrings.xml')
					)
					shared_strings = [
						''.join(node.itertext()) for node in shared_root
					]
				root = ElementTree.fromstring(
					workbook.read('xl/worksheets/sheet1.xml')
				)
				workbook_values['rows'] = [
					[
						(shared_strings[int(cell.find(
							'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v'
						).text)] if cell.get('t') == 's' else (cell.find(
							'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v'
						).text if cell.find(
							'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v'
						) is not None else ''))
						for cell in row
					]
					for row in root.iter(
						'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'
					)
				]

		blob_client.create_blob_from_path.side_effect = read_workbook
		_upload_summary_excel(
			blob_client=blob_client,
			container_name='primer-verifier-csv-test',
			request=request,
		)
		rows = workbook_values['rows']
		self.assertEqual(len(rows), 4)
		self.assertEqual(
			rows[1][:8],
			['BDS-Salm001', '2025-SEQ-0789', 'Salmonella enterica',
				'enterica', 'Sundsvall', 'INCLUSIVITY', 'BDS-Salmonella',
				'Salmonella'],
		)
		self.assertEqual(
			rows[2][:8],
			['ERR11874512', 'ERR11874512', '#N/A', 'ND', 'Orleans',
				'INCLUSIVITY', 'BDS-Salmonella', 'Salmonella'],
		)
		self.assertEqual(
			rows[3][:8],
			['BDS-Excl001', '2025-SEQ-0996', 'Enterobacter kobei',
				'ND', 'ND', 'EXCLUSIVITY', 'BDS-Exclusivity',
				'Not Salmonella'],
		)

	def test_validator_summary_workbook_includes_probe_columns(self):
		user = User.objects.create_user(username='summary-probe-test')
		request = ValidatorRequest.objects.create(
			user=user,
			probe_sequence='ACGT',
			validator_original_report=json.dumps({
				'target': {'inclusivity': {
					'strain-1': {
						'amplicon_length': '100',
						'probe': {'probe': {
							'percent_id': 75.0,
							'gaps': '1',
							'mismatches': '2',
							'mismatch_details': '2C>T',
						}},
					}
				}},
			}),
		)
		primer_set = ValidatorPrimerSet.objects.create(
			validator_request=request, primer_name='target'
		)
		ValidatorPrimers.objects.create(
			primer=primer_set, primer_header='target-F',
			primer_sequence='ACGT',
		)
		ValidatorPanel.objects.create(
			validator_request=request, genus='bds-salmonella',
			panel='inclusivity',
		)

		workbook_values = {}
		blob_client = mock.Mock()

		def read_workbook(**kwargs):
			with zipfile.ZipFile(kwargs['file_path']) as workbook:
				workbook_values['strings'] = workbook.read(
					'xl/sharedStrings.xml'
				).decode('utf-8')

		blob_client.create_blob_from_path.side_effect = read_workbook
		_upload_summary_excel(
			blob_client=blob_client,
			container_name='primer-validator-probe-test',
			request=request,
		)
		strings = workbook_values['strings']
		self.assertIn('Probe Presence  target', strings)
		self.assertIn('Probe Gaps  target', strings)
		self.assertIn('Probe Mismatches  target', strings)
		self.assertIn('Probe Mismatch Details  target', strings)
		self.assertIn('Absent', strings)
		self.assertIn('2C&gt;T', strings)

	def test_summary_workbook_adds_gene_reference_columns(self):
		user = User.objects.create_user(username='summary-gene-columns-test')
		request = ValidatorRequest.objects.create(
			user=user,
			project_name='gene columns',
			gene_exclusion_categories=['INTERNAL_STOP'],
			validator_original_report=json.dumps({
				'invA': {'inclusivity': {
					'strain-1': {'amplicon_length': '100'},
				}},
			}),
			gene_results=[{
				'report': 'strain-1_blastn_geneseekr.tsv',
				'target': 'invA', 'percent_match': '97.5',
			}],
		)
		sample = ValidatorSample.objects.create(
			validator_request=request, name='sample-1', logic='OR'
		)
		ValidatorTarget.objects.create(
			sample=sample, name='invA', gene_sequence='ATGAAA'
		)
		ValidatorPrimerSet.objects.create(
			validator_request=request, primer_name='invA'
		)

		workbook_values = {}
		blob_client = mock.Mock()

		def read_workbook(**kwargs):
			with zipfile.ZipFile(kwargs['file_path']) as workbook:
				shared_root = ElementTree.fromstring(
					workbook.read('xl/sharedStrings.xml')
				)
				shared_strings = [
					''.join(node.itertext()) for node in shared_root
				]
				root = ElementTree.fromstring(
					workbook.read('xl/worksheets/sheet1.xml')
				)
				workbook_values['rows'] = []
				for row in root.iter(
					'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'
				):
					values = []
					for cell in row:
						value = cell.find(
							'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v'
						)
						text = value.text if value is not None else ''
						if cell.get('t') == 's':
							text = shared_strings[int(text)]
						values.append(text)
					workbook_values['rows'].append(values)

		blob_client.create_blob_from_path.side_effect = read_workbook
		_upload_summary_excel(
			blob_client=blob_client,
			container_name='primer-validator-gene-columns',
			request=request,
		)
		headers = workbook_values['rows'][0]
		self.assertEqual(
			workbook_values['rows'][1][7],
			'Pass: At least one target matched'
		)
		self.assertIn('LOGIC', headers)
		self.assertIn('Matched Exclusion Categories', headers)
		self.assertNotIn('Gene category details  invA', headers)
		self.assertIn('Gene Sequence Percent Match  invA', headers)

	def test_validator_gene_task_is_skipped_without_target_reference(self):
		user = User.objects.create_user(username='validator-no-gene-task-test')
		request = ValidatorRequest.objects.create(
			user=user,
			project_name='no gene task',
			forward_primer='ACGT', reverse_primer='TGCA',
		)
		sample = ValidatorSample.objects.create(
			validator_request=request, name='sample-1'
		)
		ValidatorTarget.objects.create(
			sample=sample, name='invA',
			forward_primer='ACGT', reverse_primer='TGCA',
		)

		primer_task, gene_task = _validator_tasks(request)

		self.assertEqual(primer_task.task_type, 'primer')
		self.assertIsNone(gene_task)
		self.assertEqual(
			ValidatorAnalysisTask.objects.filter(
				validator_request=request
			).count(),
			1,
		)

	def test_validator_and_verifier_processing_urls_use_matching_keywords(self):
		self.assertEqual(
			reverse(
				'primer_finder:primer_validator_processing',
				kwargs={'validator_pk': 147}
			),
			'/en-ca/primer_finder/primer_validator_processing/147/'
		)
		self.assertEqual(
			reverse(
				'primer_finder:primer_verifier_processing',
				kwargs={'verifier_pk': 147}
			),
			'/en-ca/primer_finder/primer_verifier_processing/147/'
		)

	def test_request_container_names_match_workflow(self):
		user = User.objects.create_user(username='container-name-test')
		verifier_request = VerifierRequest.objects.create(user=user)
		validator_request = ValidatorRequest.objects.create(user=user)
		self.assertEqual(
			verifier_request.container_namer(),
			'primer-verifier-{0}'.format(verifier_request.pk)
		)
		self.assertEqual(
			validator_request.container_namer(),
			'primer-validator-{0}'.format(validator_request.pk)
		)

	def test_verifier_creates_independent_analysis_tasks(self):
		user = User.objects.create_user(username='verifier-task-rows')
		request = VerifierRequest.objects.create(
			user=user, project_name='task rows'
		)
		primer_task, gene_task = _verifier_tasks(request)
		self.assertEqual(
			set(VerifierAnalysisTask.objects.values_list('task_type', flat=True)),
			set(['primer', 'gene'])
		)
		self.assertEqual(primer_task.verifier_request_id, request.pk)
		self.assertEqual(gene_task.verifier_request_id, request.pk)

	def test_split_batch_tasks_use_distinct_resource_identifiers(self):
		user = User.objects.create_user(username='split-batch-identifiers')
		verifier_request = VerifierRequest.objects.create(user=user)
		primer_task, gene_task = _verifier_tasks(verifier_request)
		validator_request = ValidatorRequest.objects.create(user=user)
		sample = ValidatorSample.objects.create(
			validator_request=validator_request,
			name='target sample',
		)
		ValidatorTarget.objects.create(
			sample=sample,
			name='target',
			gene_sequence='ATGC',
		)
		validator_primer_task, validator_gene_task = _validator_tasks(
			validator_request
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks.generic_api_submit',
			return_value={'status': 'Success', 'job_id': 'job', 'tasks': []},
		) as submit:
			_submit_verifier_task(
				request=verifier_request,
				analysis_task=primer_task,
				command='primer command',
				vm_size='Standard_D4ds_v5',
			)
			_submit_verifier_task(
				request=verifier_request,
				analysis_task=gene_task,
				command='gene command',
				vm_size='Standard_D8s_v3',
			)
			_submit_validator_task(
				request=validator_request,
				analysis_task=validator_primer_task,
				command='validator primer command',
				vm_size='Standard_D4ds_v5',
			)
			_submit_validator_task(
				request=validator_request,
				analysis_task=validator_gene_task,
				command='validator gene command',
				vm_size='Standard_D8s_v3',
			)

		identifiers = [
			call[1]['unique_id'] for call in submit.call_args_list
		]
		self.assertEqual(
			identifiers,
			[
				'primer',
				'gene',
				'primer',
				'gene',
			],
		)

	def test_focused_verifier_commands_are_separate_and_mount_shared_inputs(self):
		request = VerifierRequest(
			mismatches=2, min_amplicon_size=0,
			max_amplicon_size=1500, range_buffer=0,
			contig_breaks=False, probe_sequence=''
		)
		primer_command = _create_focused_primer_command(
			request=request,
			container_name='primer-verifier-148',
			file_name='primers.fasta',
		)
		gene_command = _create_focused_geneseekr_command(
			container_name='primer-verifier-148',
			primer_file_name='primers.fasta',
		)
		self.assertIn('primer_validator.py', primer_command)
		self.assertNotIn('curl ', primer_command)
		self.assertIn('GeneSeekr blastn', gene_command)
		self.assertIn('GeneSeekr blastn -u -a -s ', gene_command)
		self.assertNotIn('GeneSeekr tblastx', gene_command)
		self.assertNotIn('primer_validator.py', gene_command)
		self.assertIn('$AZ_BATCH_NODE_MOUNTS_DIR/primer-verifier-148', gene_command)

	def test_focused_primer_command_includes_probe_and_contig_break_options(self):
		request = VerifierRequest(
			mismatches=2, min_amplicon_size=100,
			max_amplicon_size=1200, range_buffer=5,
			contig_breaks=True, probe_sequence='ACGT'
		)
		command = _create_focused_primer_command(
			request=request,
			container_name='primer-verifier-options',
			file_name='primers.fasta',
		)

		self.assertIn(' -cb', command)
		self.assertIn('-p $AZ_BATCH_NODE_MOUNTS_DIR/primer-verifier-options/probe.fasta', command)
		self.assertIn('-m 2 -min 100 -max 1200 -rb 5', command)

	def test_validator_submission_failure_is_reported(self):
		user = User.objects.create_user(username='validator-submit-failure')
		request = ValidatorRequest.objects.create(user=user)
		analysis_task = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer'
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks.generic_api_submit',
			return_value={'status': 'Failure', 'error': 'quota exceeded'},
		):
			with self.assertRaisesRegex(RuntimeError, 'quota exceeded'):
				_submit_validator_task(
					request=request,
					analysis_task=analysis_task,
					command='validator command',
					vm_size='Standard_D4ds_v5',
				)

		analysis_task.refresh_from_db()
		self.assertEqual(analysis_task.status, 'pending')

	def test_gene_command_downloads_each_input_blob(self):
		command = _create_validator_gene_task_command(
			request=None,
			container_name='primer-validator-inputs',
			input_blobs=[
				{
					'blob_name': 'inclusivity/panel.zip',
					'sas_url': 'https://example/panel?sig=one',
				},
				{
					'blob_name': 'targets/query.fasta',
					'sas_url': 'https://example/query?sig=two',
				},
			],
		)

		self.assertEqual(command.count('curl --fail --location --retry 5'), 2)
		self.assertIn('inclusivity/panel.zip', command)
		self.assertIn('targets/query.fasta', command)

	def test_prepare_validator_analysis_submits_tasks_with_shared_manifest(self):
		user = User.objects.create_user(username='validator-prepare-success')
		request = ValidatorRequest.objects.create(
			user=user, project_name='prepare success'
		)
		sample = ValidatorSample.objects.create(
			validator_request=request, name='invA'
		)
		ValidatorTarget.objects.create(
			sample=sample, name='invA', probe_sequence='ACGT'
		)
		input_blobs = [
			{'blob_name': 'primers.fasta', 'sas_url': 'https://example/primers'},
			{'blob_name': 'probe.fasta', 'sas_url': 'https://example/probe'},
			{'blob_name': 'targets/query.fasta', 'sas_url': 'https://example/targets'},
		]

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks.upload_primers',
			return_value=('validator-container', 'primers.fasta'),
		) as upload_primers, mock.patch(
			'olc_webportalv2.primer_finder.tasks.upload_probe'
		) as upload_probe, mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_panel_files',
			return_value=[],
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._upload_validator_gene_targets',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._generate_input_blob_sas_urls',
			return_value=input_blobs,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._submit_validator_task'
		) as submit:
			_prepare_validator_analysis(validator_request_pk=request.pk)

		upload_primers.assert_called_once_with(request=request)
		upload_probe.assert_called_once_with(request=request)
		self.assertEqual(submit.call_count, 1)
		analysis_task = ValidatorAnalysisTask.objects.get(
			validator_request=request, task_type='primer'
		)
		self.assertEqual(analysis_task.input_manifest, input_blobs)
		self.assertEqual(submit.call_args[1]['vm_size'], 'Standard_D4ds_v5')

	def test_target_probes_are_uploaded_as_target_specific_fasta_records(self):
		user = User.objects.create_user(username='validator-target-probes')
		request = ValidatorRequest.objects.create(user=user)
		inva = ValidatorSample.objects.create(
			validator_request=request, name='invA'
		)
		stn = ValidatorSample.objects.create(
			validator_request=request, name='stn'
		)
		ValidatorTarget.objects.create(
			sample=inva, name='invA', probe_sequence='ACGT'
		)
		ValidatorTarget.objects.create(
			sample=stn, name='stn', probe_sequence='TGCA'
		)

		self.assertEqual(
			_validator_probe_records(request),
			[('invA', 'ACGT'), ('stn', 'TGCA')],
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.methods.BlockBlobService'
		) as blob_service:
			upload_probe(request=request)

		blob_service.return_value.create_blob_from_bytes.assert_called_once()
		uploaded_probe = blob_service.return_value.create_blob_from_bytes.call_args[1]['blob']
		self.assertEqual(
			uploaded_probe,
			b'>invA\r\nACGT\r\n>stn\r\nTGCA\r\n',
		)

	def test_submission_error_marks_unsubmitted_validator_tasks_terminal(self):
		user = User.objects.create_user(username='validator-partial-submit')
		request = ValidatorRequest.objects.create(user=user, status='Processing')
		submitted = ValidatorAnalysisTask.objects.create(
			validator_request=request,
			task_type='primer',
			status='submitted',
			batch_job_id='primer-job',
		)
		pending = ValidatorAnalysisTask.objects.create(
			validator_request=request,
			task_type='gene',
			status='pending',
		)

		primer_tasks._handle_primer_error(
			exception=RuntimeError('gene submission failed'),
			request_model=ValidatorRequest,
			request_pk=request.pk,
		)

		request.refresh_from_db()
		submitted.refresh_from_db()
		pending.refresh_from_db()
		self.assertEqual(request.status, 'Error')
		self.assertEqual(submitted.status, 'submitted')
		self.assertEqual(pending.status, 'error')
		self.assertIn('gene submission failed', pending.error)

	def test_run_primer_validator_records_preparation_failure(self):
		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._prepare_validator_analysis',
			side_effect=RuntimeError('prepare failed'),
		) as prepare, mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_primer_error'
		) as handle_error:
			run_primer_validator.run(validator_request_pk=42)

		prepare.assert_called_once_with(validator_request_pk=42)
		self.assertEqual(handle_error.call_count, 1)
		self.assertEqual(
			handle_error.call_args[1]['request_model'], ValidatorRequest
		)
		self.assertEqual(handle_error.call_args[1]['request_pk'], 42)

	def test_process_validator_analysis_task_marks_success_and_cleans_up(self):
		user = User.objects.create_user(username='validator-poll-success')
		request = ValidatorRequest.objects.create(user=user)
		task = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer',
			status='submitted', batch_job_id='validator-job-success',
		)
		batch_client = mock.Mock()

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_tasks_completed',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_exit_codes',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._finalize_validator_request'
		) as finalize, mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		) as cleanup:
			_process_validator_analysis_task(
				batch_client=batch_client, task=task
			)

		task.refresh_from_db()
		self.assertEqual(task.status, 'complete')
		finalize.assert_called_once_with(request=request)
		cleanup.assert_called_once_with(
			batch_client=batch_client, resource_id='validator-job-success'
		)

	def test_process_validator_analysis_task_marks_nonzero_exit_as_error(self):
		user = User.objects.create_user(username='validator-poll-failure')
		request = ValidatorRequest.objects.create(user=user)
		task = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='gene',
			status='submitted', batch_job_id='validator-job-failure',
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_tasks_completed',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_exit_codes',
			return_value=False,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._finalize_validator_request'
		) as finalize, mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		):
			_process_validator_analysis_task(
				batch_client=mock.Mock(), task=task
			)

		task.refresh_from_db()
		self.assertEqual(task.status, 'error')
		self.assertIn('non-zero exit', task.error)
		finalize.assert_called_once_with(request=request)

	def test_process_validator_analysis_task_persists_polling_exception(self):
		user = User.objects.create_user(username='validator-poll-exception')
		request = ValidatorRequest.objects.create(user=user, status='Processing')
		task = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer',
			status='submitted', batch_job_id='validator-job-exception',
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_tasks_completed',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_exit_codes',
			side_effect=RuntimeError('exit code lookup failed'),
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		) as cleanup:
			_process_validator_analysis_task(
				batch_client=mock.Mock(), task=task
			)

		task.refresh_from_db()
		request.refresh_from_db()
		self.assertEqual(task.status, 'error')
		self.assertEqual(task.error, 'exit code lookup failed')
		self.assertEqual(request.status, 'Error')
		self.assertEqual(request.errors, 'exit code lookup failed')
		cleanup.assert_called_once_with(
			batch_client=mock.ANY, resource_id='validator-job-exception'
		)

	def test_process_validator_analysis_task_waits_for_incomplete_batch(self):
		user = User.objects.create_user(username='validator-poll-incomplete')
		request = ValidatorRequest.objects.create(user=user)
		task = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer',
			status='submitted', batch_job_id='validator-job-active',
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_tasks_completed',
			return_value=False,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_exit_codes'
		) as exit_codes, mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		) as cleanup:
			_process_validator_analysis_task(
				batch_client=mock.Mock(), task=task
			)

		task.refresh_from_db()
		self.assertEqual(task.status, 'submitted')
		exit_codes.assert_not_called()
		cleanup.assert_not_called()

	def test_process_legacy_validator_task_handles_success_and_deletes_row(self):
		user = User.objects.create_user(username='legacy-validator-success')
		request = ValidatorRequest.objects.create(user=user)
		legacy_task = ValidatorAzureRequest.objects.create(
			validator_request=request
		)
		batch_client = mock.Mock()

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_tasks_completed',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_exit_codes',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_successful_validator_run'
		) as successful, mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		) as cleanup:
			_process_validator_task(batch_client=batch_client, task=legacy_task)

		self.assertFalse(
			ValidatorAzureRequest.objects.filter(pk=legacy_task.pk).exists()
		)
		successful.assert_called_once_with(
			batch_job_name=request.container_namer(),
			validator_request=request,
		)
		cleanup.assert_called_once_with(
			batch_client=batch_client,
			resource_id=request.container_namer(),
			pool_id=request.container_namer(),
		)

	def test_process_legacy_validator_task_handles_failed_exit(self):
		user = User.objects.create_user(username='legacy-validator-failure')
		request = ValidatorRequest.objects.create(user=user)
		legacy_task = ValidatorAzureRequest.objects.create(
			validator_request=request
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_tasks_completed',
			return_value=True,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._check_exit_codes',
			return_value=False,
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_failed_validator_run'
		) as failed, mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		):
			_process_validator_task(
				batch_client=mock.Mock(), task=legacy_task
			)

		failed.assert_called_once_with(validator_request=request)
		self.assertFalse(
			ValidatorAzureRequest.objects.filter(pk=legacy_task.pk).exists()
		)

	def test_validator_run_handlers_email_and_cleanup_behavior(self):
		user = User.objects.create_user(username='validator-handler-email')
		success_request = ValidatorRequest.objects.create(
			user=user, project_name='handler success',
			emails_array=['one@example.org', 'two@example.org'], report='raw report'
		)
		failed_request = ValidatorRequest.objects.create(
			user=user, project_name='handler failure',
			emails_array=['one@example.org'], status='Processing'
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks.post_validator_run',
			return_value=('report-url', 'summary-url', '/tmp/run-folder'),
		), mock.patch(
			'olc_webportalv2.primer_finder.tasks.send_email'
		) as send_email, mock.patch(
			'olc_webportalv2.primer_finder.tasks.shutil.rmtree'
		) as remove_folder:
			_handle_successful_validator_run(
				batch_job_name='validator-handler-success',
				validator_request=success_request,
			)

		self.assertEqual(send_email.call_count, 2)
		remove_folder.assert_called_once_with('/tmp/run-folder')
		self.assertIn('Complete', send_email.call_args_list[0][1]['subject'])

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks.send_email'
		) as send_email:
			_handle_failed_validator_run(validator_request=failed_request)

		failed_request.refresh_from_db()
		self.assertEqual(failed_request.status, 'Error')
		self.assertEqual(send_email.call_count, 1)
		self.assertIn('Failed', send_email.call_args[1]['subject'])

	def test_check_validator_tasks_dispatches_active_and_cleanup_work(self):
		user = User.objects.create_user(username='validator-dispatcher')
		request = ValidatorRequest.objects.create(user=user)
		submitted = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer', status='submitted',
			batch_job_id='submitted-job',
		)
		terminal = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='gene', status='complete',
			batch_job_id='terminal-job',
		)
		legacy = ValidatorAzureRequest.objects.create(
			validator_request=request
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks.batch_auth.SharedKeyCredentials'
		) as credentials, mock.patch(
			'olc_webportalv2.primer_finder.tasks.batch.BatchServiceClient'
		) as client, mock.patch(
			'olc_webportalv2.primer_finder.tasks._process_validator_analysis_task'
		) as process_analysis, mock.patch(
			'olc_webportalv2.primer_finder.tasks._process_validator_task'
		) as process_legacy, mock.patch(
			'olc_webportalv2.primer_finder.tasks._delete_batch_resources'
		) as cleanup:
			check_validator_tasks.run()

		self.assertEqual(credentials.call_count, 1)
		self.assertEqual(client.call_count, 1)
		process_analysis.assert_called_once_with(
			batch_client=client.return_value, task=submitted
		)
		process_legacy.assert_called_once_with(
			batch_client=client.return_value, task=legacy
		)
		cleanup.assert_called_once_with(
			batch_client=client.return_value, resource_id='terminal-job'
		)

	def _target_data(self, total='1'):
		return {
			'targets-TOTAL_FORMS': total,
			'targets-INITIAL_FORMS': '0',
			'targets-MIN_NUM_FORMS': '1',
			'targets-MAX_NUM_FORMS': '1000',
			'targets-0-target_name': 'invA',
			'targets-0-forward_primer': 'ACGT',
			'targets-0-reverse_primer': 'TGCA',
			'targets-0-probe_sequence': '',
			'targets-0-gene_sequence': '',
		}

	def test_pasted_target_is_valid(self):
		formset = ValidatorTargetFormSetFactory(
			data=self._target_data(), prefix='targets'
		)
		self.assertTrue(formset.is_valid())

	def test_verifier_accepts_genus_seqids_and_benchmark_queries(self):
		genus = Genus.objects.create(genus='Listeria')
		SequenceData.objects.create(
			seqid='2024-TEST-0001', quality='Pass', genus='Listeria',
			species='', serotype='', mlst='', rmlst='',
		)
		form = VerifierForm(data={
			'project_name': 'query selection',
			'primer_sequences': '>target-F\nACGT\n>target-R\nTGCA\n',
			'probe_sequence': '',
			'mismatches': '2',
			'min_amplicon_size': '0',
			'max_amplicon_size': '1500',
			'contig_breaks': '',
			'range_buffer': '0',
			'inclusivity_panel': [],
			'exclusivity_panel': [],
			'inclusivity_genus': str(genus.pk),
			'inclusivity_seqids': '2024-TEST-0001',
			'inclusivity_benchmark': 'listeria',
			'exclusivity_genus': '',
			'exclusivity_seqids': '',
			'exclusivity_benchmark': '',
		})
		self.assertTrue(form.is_valid(), form.errors)
		self.assertEqual(
			form.cleaned_data['inclusivity_seqids'], ['2024-TEST-0001']
		)

	def test_verifier_rejects_unknown_query_seqid(self):
		form = VerifierForm(data={
			'primer_sequences': '>target-F\nACGT\n>target-R\nTGCA\n',
			'mismatches': '2', 'min_amplicon_size': '0',
			'max_amplicon_size': '1500', 'range_buffer': '0',
			'inclusivity_seqids': '2024-TEST-9999',
		})
		self.assertFalse(form.is_valid())
		self.assertIn('inclusivity_seqids', form.errors)

	def test_seqid_autocomplete_searches_metadata(self):
		SequenceData.objects.create(
			seqid='2024-TEST-0042', quality='Pass', genus='Listeria',
			species='', serotype='', mlst='', rmlst='',
		)
		response = self.client.get(
			reverse('metadata:seqid_autocompleter'),
			{'q': '0042'},
		)
		self.assertEqual(response.status_code, 200)
		result = response.json()['results'][0]
		self.assertEqual(result['text'], '2024-TEST-0042')
		self.assertEqual(result['selected_text'], '2024-TEST-0042')

	def test_verifier_task_resolves_query_seqids(self):
		user = User.objects.create_user(username='query-task-test')
		request = VerifierRequest.objects.create(
			user=user, project_name='query task',
			inclusivity_seqids=['2024-TEST-0001'],
		)
		VerifierPanel.objects.create(
			verifier_request=request, genus='Selected-seqids',
			panel='inclusivity'
		)
		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._copy_sequence_files',
			return_value=['inclusivity/2024-TEST-0001.fasta'],
		) as copy_sequences, mock.patch(
			'olc_webportalv2.primer_finder.tasks.create_empty_folder'
		):
			_handle_panel_files(
				container_name='primer-verifier-test', inclusivity=True,
				panel_model=VerifierPanel, request_pk=request.pk,
			)
		copy_sequences.assert_called_once_with(
			container_name='primer-verifier-test', panel='inclusivity',
			seqids=['2024-TEST-0001'],
		)

	def test_salmonella_rule_requires_invA_or_stn(self):
		formset = ValidatorTargetFormSetFactory(
			data=self._target_data(), prefix='targets'
		)
		formset.classification_rule = 'salmonella'
		self.assertTrue(formset.is_valid())

	def test_generic_logic_requires_two_targets(self):
		formset = ValidatorTargetFormSetFactory(
			data=self._target_data(), prefix='targets'
		)
		formset.logic = 'OR'
		self.assertFalse(formset.is_valid())
		self.assertIn(
			'The selected logic rule requires at least two targets.',
			formset.non_form_errors(),
		)

	def test_generic_logic_accepts_two_targets(self):
		data = self._target_data(total='2')
		data.update({
			'targets-1-target_name': 'stn',
			'targets-1-forward_primer': 'AAAA',
			'targets-1-reverse_primer': 'TTTT',
			'targets-1-probe_sequence': '',
			'targets-1-gene_sequence': '',
		})
		formset = ValidatorTargetFormSetFactory(
			data=data, prefix='targets'
		)
		formset.logic = 'OR'
		self.assertTrue(formset.is_valid())

	def test_classification_rule_disables_logic_semantics(self):
		form = ValidatorExperimentForm(data={
			'logic': 'OR', 'classification_rule': 'salmonella',
			'mismatches': '3', 'min_amplicon_size': '0',
			'max_amplicon_size': '1500', 'range_buffer': '0',
			'inclusivity_panel': ['bds-salmonella'],
		})
		self.assertTrue(form.is_valid())
		self.assertEqual(form.cleaned_data['logic'], '')

	def test_generic_logic_and_salmonella_precedence(self):
		self.assertEqual(
			_validator_classification_label({'inva'}, {'inva', 'stn'}, 'AND', ''),
			'Not detected',
		)
		self.assertEqual(
			_validator_classification_label({'inva'}, {'inva', 'stn'}, 'OR', ''),
			'Detected',
		)
		self.assertEqual(
			_validator_classification_label(
				{'inva'}, {'inva', 'stn'}, 'OR', 'salmonella'
			),
			'Salmonella'
		)
		self.assertEqual(
			_validator_classification_label(
				{'stn'}, {'inva', 'stn'}, 'AND', 'salmonella'
			),
			'Salmonella'
		)
		self.assertEqual(
			_validator_classification_label(
				{'extra'}, {'inva', 'stn'}, 'OR', 'salmonella'
			),
			'Not Salmonella'
		)

	def test_logic_labels_report_all_match_states(self):
		self.assertEqual(
			_validator_logic_label({'inva', 'stn'}, {'inva', 'stn'}, 'AND'),
			'Pass: All targets matched'
		)
		self.assertEqual(
			_validator_logic_label({'inva'}, {'inva', 'stn'}, 'AND'),
			'Fail: At least one target matched'
		)
		self.assertEqual(
			_validator_logic_label({'inva'}, {'inva', 'stn'}, 'OR'),
			'Pass: At least one target matched'
		)
		self.assertEqual(
			_validator_logic_label(set(), {'inva', 'stn'}, 'OR'),
			'Fail: No targets matched'
		)

	def test_salmonella_rule_allows_extra_targets(self):
		data = self._target_data(total='3')
		data.update({
			'targets-1-target_name': 'stn',
			'targets-1-forward_primer': 'AAAA',
			'targets-1-reverse_primer': 'TTTT',
			'targets-1-probe_sequence': '',
			'targets-1-gene_sequence': '',
			'targets-2-target_name': 'extra',
			'targets-2-forward_primer': 'CCCC',
			'targets-2-reverse_primer': 'GGGG',
			'targets-2-probe_sequence': '',
			'targets-2-gene_sequence': '',
		})
		formset = ValidatorTargetFormSetFactory(data=data, prefix='targets')
		formset.classification_rule = 'salmonella'
		self.assertTrue(formset.is_valid())

	def test_missing_classification_target_is_shown_on_request_page(self):
		user = User.objects.create_user(username='validator-error-test')
		data = {
			'project_name': 'missing stn',
			'mismatches': '2',
			'classification_rule': 'salmonella',
			'logic': '',
			'targets-TOTAL_FORMS': '1',
			'targets-INITIAL_FORMS': '0',
			'targets-MIN_NUM_FORMS': '1',
			'targets-MAX_NUM_FORMS': '1000',
			'targets-0-target_name': 'stnA',
			'targets-0-forward_primer': 'ACGT',
			'targets-0-reverse_primer': 'TGCA',
			'targets-0-probe_sequence': '',
			'targets-0-gene_sequence': '',
		}
		self.client.force_login(user)
		response = self.client.post(
			reverse('primer_finder:primer_validator_request'), data
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			'requires at least one of these targets: inva, stn'
		)

	def test_gene_category_exclusions_require_a_gene_reference(self):
		user = User.objects.create_user(username='validator-category-reference-test')
		data = {
			'project_name': 'categories without reference',
			'classification_rule': 'none',
			'logic': '',
			'gene_exclusion_categories': ['INTERNAL_STOP'],
			'targets-TOTAL_FORMS': '1',
			'targets-INITIAL_FORMS': '0',
			'targets-MIN_NUM_FORMS': '1',
			'targets-MAX_NUM_FORMS': '1000',
			'targets-0-target_name': 'invA',
			'targets-0-forward_primer': 'ACGT',
			'targets-0-reverse_primer': 'TGCA',
			'targets-0-probe_sequence': '',
			'targets-0-gene_sequence': '',
		}
		self.client.force_login(user)
		response = self.client.post(
			reverse('primer_finder:primer_validator_request'), data
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			'require at least one pasted gene reference sequence or uploaded FASTA file'
		)
		self.assertFalse(
			ValidatorRequest.objects.filter(
				project_name='categories without reference'
			).exists()
		)

	@mock.patch('olc_webportalv2.primer_finder.views.run_primer_validator')
	def test_valid_validator_request_persists_targets_and_queues_task(
		self, run_validator
	):
		user = User.objects.create_user(username='validator-create-test')
		data = self._target_data()
		data.update({
			'project_name': 'valid validator request',
			'classification_rule': 'none',
			'logic': '',
			'mismatches': '3',
			'min_amplicon_size': '0',
			'max_amplicon_size': '1500',
			'range_buffer': '0',
			'contig_breaks': '',
			'inclusivity_panel': ['bds-salmonella'],
			'exclusivity_panel': [],
		})
		data['targets-0-probe_sequence'] = 'ACGT'
		self.client.force_login(user)

		response = self.client.post(
			reverse('primer_finder:primer_validator_request'), data
		)

		self.assertEqual(response.status_code, 302)
		request = ValidatorRequest.objects.get(
			project_name='valid validator request'
		)
		self.assertEqual(request.status, 'Processing')
		self.assertEqual(request.primer_sequences, '>invA-F\nACGT\n>invA-R\nTGCA')
		self.assertEqual(
			list(request.samples.values_list('name', flat=True)), ['invA']
		)
		self.assertEqual(
			list(request.samples.get().targets.values_list('name', flat=True)),
			['invA'],
		)
		self.assertEqual(request.probe_sequence, '')
		self.assertEqual(
			request.samples.get().targets.get().probe_sequence, 'ACGT'
		)
		self.assertEqual(
			list(request.primer_id.values_list(
				'primer_name', flat=True
			)),
			['invA'],
		)
		run_validator.apply_async.assert_called_once_with(
			queue='primer_finder',
			kwargs={'validator_request_pk': request.pk},
			countdown=10,
		)

	def test_invalid_experiment_and_target_errors_are_shown_together(self):
		user = User.objects.create_user(username='validator-all-errors-test')
		data = {
			'project_name': 'all errors',
			'classification_rule': 'salmonella',
			'targets-TOTAL_FORMS': '1',
			'targets-INITIAL_FORMS': '0',
			'targets-MIN_NUM_FORMS': '1',
			'targets-MAX_NUM_FORMS': '1000',
			'targets-0-target_name': 'stnA',
			'targets-0-forward_primer': '',
			'targets-0-reverse_primer': '',
		}
		self.client.force_login(user)
		response = self.client.post(
			reverse('primer_finder:primer_validator_request'), data
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Maximum Number of Mismatches')
		self.assertContains(
			response,
			'requires at least one of these targets: inva, stn'
		)
		self.assertContains(response, 'A forward primer is required.')

	def test_validator_defaults_are_rendered(self):
		form = ValidatorExperimentForm()
		self.assertEqual(form.fields['mismatches'].initial, 3)
		self.assertEqual(form.fields['min_amplicon_size'].initial, 0)
		self.assertEqual(form.fields['max_amplicon_size'].initial, 1500)
		self.assertEqual(form.fields['range_buffer'].initial, 0)
		self.assertFalse(form.fields['contig_breaks'].initial)
		rendered_form = form.as_p()
		self.assertIn('value="3"', rendered_form)
		self.assertIn('value="0"', rendered_form)
		self.assertIn('value="1500"', rendered_form)

	def test_validator_request_page_renders_defaults(self):
		user = User.objects.create_user(username='validator-defaults-test')
		self.client.force_login(user)
		response = self.client.get(
			reverse('primer_finder:primer_validator_request')
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="mismatches" value="3"')
		self.assertContains(response, 'name="min_amplicon_size" value="0"')
		self.assertContains(response, 'name="max_amplicon_size" value="1500"')
		self.assertContains(response, 'name="range_buffer" value="0"')

	def test_duplicate_target_is_rejected(self):
		data = self._target_data(total='2')
		data.update({
			'targets-1-target_name': 'invA',
			'targets-1-forward_primer': 'AAAA',
			'targets-1-reverse_primer': 'TTTT',
			'targets-1-probe_sequence': '',
			'targets-1-gene_sequence': '',
		})
		formset = ValidatorTargetFormSetFactory(data=data, prefix='targets')
		self.assertFalse(formset.is_valid())

	def test_target_fasta_rejects_invalid_sequence(self):
		form = ValidatorTargetForm(
			data={
				'logic': '',
				'classification_rule': 'none', 'target_name': 'invA',
				'forward_primer': 'ACGT', 'reverse_primer': 'TGCA',
			},
			files={'reference_fasta': SimpleUploadedFile(
				'genes.fasta', b'>invA\nACGTX\n', content_type='text/plain'
			)},
		)
		self.assertFalse(form.is_valid())
		self.assertIn('reference_fasta', form.errors)

	def test_panels_cannot_overlap(self):
		choice = ValidatorExperimentForm.base_fields[
			'inclusivity_panel'
		].choices[0][0]
		form = ValidatorExperimentForm(data={
			'mismatches': '2', 'inclusivity_panel': [choice],
			'exclusivity_panel': [choice],
		})
		self.assertFalse(form.is_valid())
		self.assertTrue(form.non_field_errors())

	def test_target_fasta_accepts_gene_record(self):
		form = ValidatorTargetForm(
			data={
				'logic': '',
				'classification_rule': 'none', 'target_name': 'invA',
				'forward_primer': 'ACGT', 'reverse_primer': 'TGCA',
			},
			files={'reference_fasta': SimpleUploadedFile(
				'mixed.fasta',
				b'>target-invA\nACGT\n',
				content_type='text/plain',
			)},
		)
		self.assertTrue(form.is_valid())

	def test_gene_reports_are_normalized_from_json_and_tsv(self):
		results = _normalize_gene_reports({
			'hits.json': '[{"Query": "sample__invA", "Gene": "invA", '
			'"Identity": 99.5}]',
			'hits.tsv': 'query\ttarget\tcoverage\n'
				'sample__stn\tstn\t98\n',
		})
		self.assertEqual(len(results), 2)
		by_query = dict((result['query'], result) for result in results)
		self.assertEqual(by_query['sample__invA']['target'], 'invA')
		self.assertEqual(by_query['sample__stn']['coverage'], '98')

	def test_gene_reports_normalize_blast_column_names(self):
		results = _normalize_gene_reports({
			'hits.tsv': 'query_id\tsubject_id\tpercent_match\n'
				'Contig_10_139.646\tinvA\t99.13\n',
		})
		self.assertEqual(results[0]['query'], 'Contig_10_139.646')
		self.assertEqual(results[0]['target'], 'invA')
		self.assertEqual(results[0]['hit'], 'invA')
		self.assertEqual(results[0]['identity'], '99.13')

	def test_gene_target_creates_independent_gene_task(self):
		user = User.objects.create_user(username='validator-task-test')
		request = ValidatorRequest.objects.create(
			user=user,
			project_name='task split',
			forward_primer='ACGT',
			reverse_primer='TGCA',
		)
		sample = ValidatorSample.objects.create(
			validator_request=request,
			name='sample-1',
		)
		ValidatorTarget.objects.create(
			sample=sample,
			name='invA',
			forward_primer='ACGT',
			reverse_primer='TGCA',
			gene_sequence='ACGTACGT',
		)

		primer_task, gene_task = _validator_tasks(request)

		self.assertEqual(primer_task.task_type, 'primer')
		self.assertEqual(gene_task.task_type, 'gene')
		self.assertEqual(
			ValidatorAnalysisTask.objects.filter(
				validator_request=request,
			).count(),
			2,
		)

	@mock.patch('olc_webportalv2.primer_finder.tasks.BlockBlobService')
	def test_upload_only_gene_reference_is_sent_to_gene_task(self, blob_service):
		user = User.objects.create_user(username='validator-upload-only-test')
		request = ValidatorRequest.objects.create(
			user=user, project_name='upload-only reference',
			forward_primer='ACGT', reverse_primer='TGCA',
		)
		sample = ValidatorSample.objects.create(
			validator_request=request, name='sample-1'
		)
		target = ValidatorTarget.objects.create(
			sample=sample, name='invA',
			forward_primer='ACGT', reverse_primer='TGCA',
		)
		target.reference_fasta.save(
			'reference.fasta',
			SimpleUploadedFile(
				'reference.fasta', b'>reference-invA\nACGTACGT\n',
				content_type='text/plain',
			),
			save=True,
		)

		self.assertTrue(_upload_validator_gene_targets(
			container_name='primer-validator-upload-only', request=request
		))
		blob = blob_service.return_value.create_blob_from_bytes.call_args[1]
		self.assertIn(b'>sample-1__invA__reference-invA', blob['blob'])
		self.assertIn(b'ACGTACGT', blob['blob'])

	def test_gene_command_has_separate_report_output(self):
		command = _create_validator_gene_task_command(
			request=None,
			container_name='primer-verifier-1',
			input_blobs=[{
				'blob_name': 'targets/query.fasta',
				'sas_url': 'https://example/query?sig=value',
			}],
		)

		self.assertIn('GeneSeekr blastn', command)
		self.assertIn('GeneSeekr blastn -u -a -s ', command)
		self.assertNotIn('GeneSeekr tblastx', command)
		self.assertIn('reports/gene', command)
		self.assertNotIn("'$AZ_BATCH_NODE_MOUNTS_DIR", command)
		self.assertIn('targets/query.fasta', command)

	def test_blastn_categories_and_report_filter(self):
		records = [{
			'query': 'strain-1.fasta',
			'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon',
			'query_start': '1',
			'query_end': '9',
			'query_length': '9',
			'percent_match': '95',
			'subject_start': '1',
			'subject_end': '9',
			'subject_length': '12',
			'query_sequence': 'ATGTAGAAA',
			'raw': {},
		}]
		categories = categorize_gene_hits(records)
		self.assertEqual(
			set(categories['strain-1']),
			set(('INTERNAL_STOP', 'PARTIAL_CONTIG_END')),
		)
		report = json.dumps({
			'primer': {'inclusivity': {
				'strain-1': {'amplicon_length': '100'},
				'strain-2': {'amplicon_length': '100'},
			}},
		})
		filtered = json.loads(filter_report_by_gene_categories(
			report, categories, ('INTERNAL_STOP',)
		))
		self.assertNotIn('strain-1', filtered['primer']['inclusivity'])
		self.assertIn('strain-2', filtered['primer']['inclusivity'])

	def test_salmonella_gene_exclusions_ignore_non_invA_stn_targets(self):
		records = [{
			'query': 'strain-1',
			'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon',
			'query_start': '1', 'query_end': '9', 'query_length': '9',
			'percent_match': '95',
			'subject_start': '1', 'subject_end': '12',
			'subject_length': '12', 'query_sequence': 'ATGTAGAAA',
		}, {
			'query': 'strain-1',
			'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'L16014.1_stn',
			'query_start': '1', 'query_end': '9', 'query_length': '9',
			'percent_match': '95',
			'subject_start': '1', 'subject_end': '12',
			'subject_length': '12', 'query_sequence': 'ATGTAGAAA',
		}]
		categories = categorize_gene_hits(
			records, allowed_targets=('inva', 'stn')
		)
		self.assertEqual(
			set(categories['strain-1']), {'INTERNAL_STOP', 'EXTENDED'}
		)

	def test_gene_category_details_identify_gene_and_trigger(self):
		records = [{
			'query': 'Contig_49_225.222',
			'report': '2025-SEQ-0799_blastn_geneseekr.tsv',
			'target': 'L16014.1_stn', 'percent_match': '94.24',
			'query_start': '5404', 'query_end': '6063',
			'subject_start': '1', 'subject_end': '660',
			'subject_length': '660',
			'query_sequence': 'ATG' * 30 + 'TAA' + 'ATG' * 189,
		}, {
			'query': 'Contig_63_51.1275',
			'report': '2025-SEQ-0799_blastn_geneseekr.tsv',
			'target': 'gb|M90846.1|', 'percent_match': '9.11',
			'query_start': '163899', 'query_end': '164164',
			'subject_start': '310', 'subject_end': '575',
			'subject_length': '1998', 'query_sequence': 'ATG' * 90,
		}]
		details = categorize_gene_hit_details(records)
		evidence = details['2025-SEQ-0799']['L16014.1_stn'][0]
		self.assertEqual(evidence['category'], 'INTERNAL_STOP')
		self.assertEqual(evidence['hit']['query'], 'Contig_49_225.222')
		self.assertEqual(evidence['hit']['identity'], 94.24)
		self.assertNotIn('gb|M90846.1|', details['2025-SEQ-0799'])

	def test_internal_stop_details_include_subject_location_and_codon_change(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '100', 'query_end': '111',
			'subject_start': '1', 'subject_end': '12',
			'subject_length': '12',
			'query_sequence': 'ATGTAAAACCTT',
			'subject_sequence': 'ATGTATAAACCT',
		}]
		details = categorize_gene_hit_details(records)
		evidence = details['strain-1']['operon'][0]
		self.assertEqual(
			evidence['stop_evidence'], [{
				'query_location': '103-105',
				'subject_location': '4-6',
				'codon_change': 'TAT -> TAA',
			}]
		)

	def test_blastn_ordinary_categories_use_best_reference_coverage(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'subject_start': '1', 'subject_end': '80',
			'subject_length': '100', 'query_sequence': 'ATG' * 27,
		}, {
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '12',
			'subject_start': '1', 'subject_end': '20',
			'subject_length': '100', 'query_sequence': 'ATGTAGAAA',
		}]
		self.assertNotIn(
			'INTERNAL_STOP',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_reverse_hit_is_translated_in_reference_orientation(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '100',
			'query_start': '9', 'query_end': '1', 'query_length': '9',
			'subject_start': '9', 'subject_end': '1', 'subject_length': '9',
			'query_sequence': 'TTATTTCAT',
		}]
		self.assertEqual(categorize_gene_hits(records), {})

	def test_blastn_full_reference_without_terminal_stop_is_extended(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '100',
			'query_start': '1', 'query_end': '9', 'query_length': '9',
			'subject_start': '1', 'subject_end': '9', 'subject_length': '9',
			'query_sequence': 'ATGAAACCC',
		}]
		self.assertEqual(
			categorize_gene_hits(records).get('strain-1'), ['EXTENDED']
		)

	def test_blastn_short_terminal_fragment_is_not_extended(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '100',
			'subject_start': '92', 'subject_end': '100',
			'subject_length': '100', 'query_sequence': 'ATGAAACCC',
		}]
		self.assertNotIn(
			'EXTENDED',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_frame_shift_checks_adjacent_reference_fragments(self):
		records = [{
			'query': 'contig-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '1', 'query_end': '9', 'query_length': '9',
			'subject_start': '1', 'subject_end': '9', 'subject_length': '18',
			'query_sequence': 'ATGAAACCC',
		}, {
			'query': 'contig-2', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '2', 'query_end': '10', 'query_length': '10',
			'subject_start': '10', 'subject_end': '18', 'subject_length': '18',
			'query_sequence': 'ATGCCCTTT',
		}]
		self.assertIn(
			'FRAME_SHIFT',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_overlapping_reference_hits_are_not_frame_shift(self):
		records = [{
			'query': 'contig-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '59913', 'query_end': '60179',
			'query_length': '200000', 'subject_start': '310',
			'subject_end': '576', 'subject_length': '1998',
			'query_sequence': 'ATG' * 89,
		}, {
			'query': 'contig-2', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '99',
			'query_start': '122574', 'query_end': '124571',
			'query_length': '200000', 'subject_start': '1998',
			'subject_end': '1', 'subject_length': '1998',
			'query_sequence': 'ATG' * 666,
		}]
		self.assertNotIn(
			'FRAME_SHIFT',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_ten_base_reference_gap_is_not_frame_shift(self):
		records = [{
			'query': 'contig-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '1', 'query_end': '9', 'subject_start': '1',
			'subject_end': '9', 'subject_length': '28',
			'query_sequence': 'ATGAAACCC',
		}, {
			'query': 'contig-2', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '1', 'query_end': '9', 'subject_start': '20',
			'subject_end': '28', 'subject_length': '28',
			'query_sequence': 'ATGCCCTTT',
		}]
		self.assertNotIn(
			'FRAME_SHIFT',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_mixed_strand_fragments_are_not_frame_shift(self):
		records = [{
			'query': 'contig-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '1', 'query_end': '9', 'query_length': '20',
			'subject_start': '1', 'subject_end': '9', 'subject_length': '18',
			'query_sequence': 'ATGAAACCC',
		}, {
			'query': 'contig-2', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '19', 'query_end': '11', 'query_length': '20',
			'subject_start': '10', 'subject_end': '18', 'subject_length': '18',
			'query_sequence': 'ATGAAACCC',
		}]
		self.assertNotIn(
			'FRAME_SHIFT',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_contained_secondary_hit_is_not_frame_shift(self):
		records = [{
			'query': 'Contig_36_58.5159',
			'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'gb|M90846.1|', 'percent_match': '99.1',
			'query_start': '5423', 'query_end': '7420',
			'subject_start': '1', 'subject_end': '1998',
			'subject_length': '1998',
			'query_sequence': 'ATG' * 666,
		}, {
			'query': 'Contig_133_37.7363',
			'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'gb|M90846.1|', 'percent_match': '9.11',
			'query_start': '14303', 'query_end': '14568',
			'subject_start': '310', 'subject_end': '575',
			'subject_length': '1998',
			'query_sequence': 'ATG' * 90,
		}]
		self.assertNotIn(
			'FRAME_SHIFT',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_gene_hits_below_ninety_percent_identity_are_ignored(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '89.99',
			'query_start': '1', 'query_end': '9',
			'subject_start': '1', 'subject_end': '9',
			'subject_length': '9', 'query_sequence': 'ATGTAGAAA',
		}]
		self.assertEqual(categorize_gene_hits(records), {})

	def test_gene_hits_at_ninety_percent_identity_are_retained(self):
		records = [{
			'query': 'strain-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '90',
			'query_start': '1', 'query_end': '9',
			'subject_start': '1', 'subject_end': '9',
			'subject_length': '9', 'query_sequence': 'ATGTAGAAA',
		}]
		self.assertIn(
			'INTERNAL_STOP',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_partial_end_is_reported_with_another_contig(self):
		records = [{
			'query': 'contig-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '1', 'query_end': '100', 'query_length': '100',
			'query_sequence': 'ATG' * 33,
			'subject_start': '1', 'subject_end': '100',
			'subject_length': '200',
		}, {
			'query': 'contig-2', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'subject_start': '101', 'subject_end': '200',
			'subject_length': '200',
		}]
		self.assertIn(
			'PARTIAL_CONTIG_END',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_blastn_partial_end_without_completion_is_flagged(self):
		records = [{
			'query': 'contig-1', 'report': 'strain-1_blastn_geneseekr.tsv',
			'target': 'operon', 'percent_match': '95',
			'query_start': '1', 'query_end': '100', 'query_length': '100',
			'query_sequence': 'ATG' * 33,
			'subject_start': '1', 'subject_end': '100',
			'subject_length': '200',
		}]
		self.assertIn(
			'PARTIAL_CONTIG_END',
			categorize_gene_hits(records).get('strain-1', []),
		)

	def test_batch_cleanup_ignores_missing_resources(self):
		batch_client = mock.Mock()
		batch_client.job.delete.side_effect = Exception(
			'The specified job does not exist.'
		)

		_delete_batch_resources(
			batch_client=batch_client, resource_id='primer-validator-1-gene'
		)

		batch_client.pool.delete.assert_called_once_with(
			pool_id='primer-validator-1-gene-pool'
		)

	def test_batch_cleanup_continues_when_job_delete_fails(self):
		batch_client = mock.Mock()
		batch_client.job.delete.side_effect = Exception('temporary failure')

		_delete_batch_resources(
			batch_client=batch_client, resource_id='primer-validator-1-gene'
		)

		batch_client.pool.delete.assert_called_once_with(
			pool_id='primer-validator-1-gene-pool'
		)

	def test_batch_cleanup_maps_job_id_to_pool_id(self):
		batch_client = mock.Mock()

		_delete_batch_resources(
			batch_client=batch_client,
			resource_id='primer-validator-1-gene-job'
		)

		batch_client.pool.delete.assert_called_once_with(
			pool_id='primer-validator-1-gene-pool'
		)

	def test_experiment_tables_use_their_matching_request_models(self):
		user = User.objects.create_user(username='table-routing-test')
		VerifierRequest.objects.create(
			user=user, project_name='verifier experiment'
		)
		ValidatorRequest.objects.create(
			user=user,
			project_name='validator experiment',
			forward_primer='ACGT',
			reverse_primer='TGCA',
		)
		self.client.force_login(user)

		validator_response = self.client.get(
			reverse('primer_finder:primer_validator_home')
		)
		verifier_response = self.client.get(
			reverse('primer_finder:primer_verifier_home')
		)

		self.assertContains(validator_response, 'validator experiment')
		self.assertNotContains(validator_response, 'verifier experiment')
		self.assertContains(verifier_response, 'verifier experiment')
		self.assertNotContains(verifier_response, 'validator experiment')

	def test_validator_home_deletes_selected_project(self):
		user = User.objects.create_user(username='validator-delete-test')
		request = ValidatorRequest.objects.create(
			user=user, project_name='delete this project',
			forward_primer='ACGT', reverse_primer='TGCA',
		)
		self.client.force_login(user)

		response = self.client.post(
			reverse('primer_finder:primer_validator_home'),
			{'delete': request.pk},
		)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(
			ValidatorRequest.objects.filter(pk=request.pk).exists()
		)

	def test_primer_home_requires_login_and_renders_for_authenticated_user(self):
		response = self.client.get(reverse('primer_finder:primer_home'))
		self.assertEqual(response.status_code, 302)

		user = User.objects.create_user(username='primer-home-view')
		self.client.force_login(user)
		response = self.client.get(reverse('primer_finder:primer_home'))
		self.assertEqual(response.status_code, 200)

	def test_verifier_home_deletes_selected_project(self):
		user = User.objects.create_user(username='verifier-delete-view')
		request = VerifierRequest.objects.create(
			user=user, project_name='delete verifier project'
		)
		self.client.force_login(user)

		response = self.client.post(
			reverse('primer_finder:primer_verifier_home'),
			{'delete': request.pk},
		)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(VerifierRequest.objects.filter(pk=request.pk).exists())

	def test_validator_processing_saves_email_only_once(self):
		user = User.objects.create_user(username='validator-email-test')
		request = ValidatorRequest.objects.create(
			user=user, project_name='email subscription', emails_array=[]
		)
		self.client.force_login(user)
		url = reverse(
			'primer_finder:primer_validator_processing',
			kwargs={'validator_pk': request.pk},
		)

		first_response = self.client.post(url, {'email': 'alerts@example.org'})
		self.assertEqual(first_response.status_code, 200)
		request.refresh_from_db()
		self.assertEqual(request.emails_array, ['alerts@example.org'])

		second_response = self.client.post(url, {'email': 'alerts@example.org'})
		self.assertEqual(second_response.status_code, 200)
		request.refresh_from_db()
		self.assertEqual(request.emails_array, ['alerts@example.org'])

	def test_validator_processing_error_page_uses_existing_home_route(self):
		user = User.objects.create_user(username='validator-processing-route')
		validator_request = ValidatorRequest.objects.create(
			user=user,
			status='Error',
		)
		self.client.force_login(user)

		response = self.client.get(
			reverse(
				'primer_finder:primer_validator_processing',
				kwargs={'validator_pk': validator_request.pk},
			)
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'PrimerValidator Home')

	def test_verifier_processing_saves_email_only_once(self):
		user = User.objects.create_user(username='verifier-email-view')
		request = VerifierRequest.objects.create(
			user=user, project_name='verifier email', emails_array=[]
		)
		self.client.force_login(user)
		url = reverse(
			'primer_finder:primer_verifier_processing',
			kwargs={'verifier_pk': request.pk},
		)

		first_response = self.client.post(url, {'email': 'alerts@example.org'})
		self.assertEqual(first_response.status_code, 200)
		request.refresh_from_db()
		self.assertEqual(request.emails_array, ['alerts@example.org'])

		second_response = self.client.post(url, {'email': 'alerts@example.org'})
		self.assertEqual(second_response.status_code, 200)
		request.refresh_from_db()
		self.assertEqual(request.emails_array, ['alerts@example.org'])

	def test_verifier_request_persists_project_and_queues_task(self):
		user = User.objects.create_user(username='verifier-request-view')
		data = {
			'project_name': 'verifier request',
			'primer_sequences': '>target-F\nACGT\n>target-R\nTGCA\n',
			'probe_sequence': '',
			'mismatches': '2',
			'min_amplicon_size': '0',
			'max_amplicon_size': '1500',
			'contig_breaks': '',
			'range_buffer': '0',
			'inclusivity_panel': ['bds-salmonella'],
			'exclusivity_panel': [],
			'inclusivity_genus': '',
			'exclusivity_genus': '',
			'inclusivity_benchmark': '',
			'exclusivity_benchmark': '',
		}
		self.client.force_login(user)

		with mock.patch(
			'olc_webportalv2.primer_finder.views.populate_primer_sets'
		), mock.patch(
			'olc_webportalv2.primer_finder.views.populate_primer_details'
		), mock.patch(
			'olc_webportalv2.primer_finder.views.populate_panel'
		), mock.patch(
			'olc_webportalv2.primer_finder.views.run_primer_verifier'
		) as run_verifier:
			response = self.client.post(
				reverse('primer_finder:primer_verifier_request'), data
			)

		self.assertEqual(response.status_code, 302)
		request = VerifierRequest.objects.get(project_name='verifier request')
		self.assertEqual(request.status, 'Processing')
		self.assertEqual(
			request.primer_sequences.replace('\r\n', '\n'),
			'>target-F\nACGT\n>target-R\nTGCA\n',
		)
		run_verifier.apply_async.assert_called_once_with(
			queue='primer_finder',
			kwargs={'verifier_request_pk': request.pk},
			countdown=10,
		)

	def test_invalid_verifier_request_does_not_queue_task(self):
		user = User.objects.create_user(username='verifier-invalid-view')
		self.client.force_login(user)
		with mock.patch(
			'olc_webportalv2.primer_finder.views.run_primer_verifier'
		) as run_verifier:
			response = self.client.post(
				reverse('primer_finder:primer_verifier_request'),
				{'primer_sequences': 'not fasta'},
			)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context['form'].errors)
		run_verifier.apply_async.assert_not_called()
		self.assertFalse(
			VerifierRequest.objects.filter(user=user).exists()
		)

	def test_key_views_redirect_anonymous_users_to_login(self):
		urls = [
			reverse('primer_finder:primer_home'),
			reverse('primer_finder:primer_verifier_home'),
			reverse('primer_finder:primer_validator_home'),
			reverse('primer_finder:primer_validator_request'),
		]

		for url in urls:
			response = self.client.get(url)
			self.assertEqual(response.status_code, 302)
			self.assertIn('/accounts/login/', response.url)

	def test_verifier_processing_initializes_missing_email_array(self):
		user = User.objects.create_user(username='verifier-null-email-array')
		request = VerifierRequest.objects.create(
			user=user, project_name='null email array', emails_array=None
		)
		self.client.force_login(user)
		url = reverse(
			'primer_finder:primer_verifier_processing',
			kwargs={'verifier_pk': request.pk},
		)

		response = self.client.post(url, {'email': 'new@example.org'})

		self.assertEqual(response.status_code, 200)
		request.refresh_from_db()
		self.assertEqual(request.emails_array, ['new@example.org'])

	def test_verifier_results_handles_empty_primer_and_panel_sets(self):
		user = User.objects.create_user(username='verifier-empty-results')
		request = VerifierRequest.objects.create(
			user=user, project_name='empty results', summary='{}'
		)
		self.client.force_login(user)

		response = self.client.get(reverse(
			'primer_finder:primer_verifier_results',
			kwargs={'verifier_pk': request.pk},
		))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['primers'], [])
		self.assertEqual(response.context['clean_primers'], {})
		self.assertEqual(response.context['panels'], [])
		self.assertEqual(response.context['genera'], [])

	def test_verifier_results_normalizes_names_and_renders_panel_context(self):
		user = User.objects.create_user(username='verifier-results-view')
		request = VerifierRequest.objects.create(
			user=user,
			project_name='verifier results',
			summary=json.dumps({'target': {'mismatches': {}}}),
		)
		VerifierPrimerSet.objects.create(
			verifier_request=request, primer_name='1-target_alpha'
		)
		VerifierPanel.objects.create(
			verifier_request=request, panel='inclusivity', genus='Salmonella'
		)
		self.client.force_login(user)

		response = self.client.get(reverse(
			'primer_finder:primer_verifier_results',
			kwargs={'verifier_pk': request.pk},
		))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['primers'], ['1-target_alpha'])
		self.assertEqual(
			response.context['clean_primers']['1-target_alpha'],
			'a1targetalpha',
		)
		self.assertEqual(response.context['panels'], ['inclusivity'])
		self.assertEqual(response.context['genera'], ['salmonella'])

	def test_finder_home_deletes_selected_project(self):
		user = User.objects.create_user(username='finder-delete-view')
		request = VerifierRequest.objects.create(
			user=user, project_name='delete finder project'
		)
		self.client.force_login(user)

		request_factory = RequestFactory()
		http_request = request_factory.post(
			'/finder/', {'delete': request.pk}
		)
		http_request.user = user
		response = primer_views.finder_home(http_request)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(VerifierRequest.objects.filter(pk=request.pk).exists())

	def test_validator_results_normalizes_primer_names_and_panel_data(self):
		user = User.objects.create_user(username='validator-results-view')
		request = ValidatorRequest.objects.create(
			user=user,
			project_name='results view',
			summary=json.dumps({'target': {'mismatches': {}}}),
		)
		ValidatorPrimerSet.objects.create(
			validator_request=request, primer_name='1-target_alpha'
		)
		ValidatorPanel.objects.create(
			validator_request=request, panel='inclusivity', genus='Salmonella'
		)
		self.client.force_login(user)

		response = self.client.get(reverse(
			'primer_finder:primer_validator_results',
			kwargs={'validator_pk': request.pk},
		))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['primers'], ['1-target_alpha'])
		self.assertEqual(
			response.context['clean_primers']['1-target_alpha'],
			'a1targetalpha',
		)
		self.assertEqual(response.context['panels'], ['inclusivity'])
		self.assertEqual(response.context['genera'], ['salmonella'])

	def test_validator_report_populates_totals_and_primer_sequences(self):
		user = User.objects.create_user(username='validator-report-view')
		request = ValidatorRequest.objects.create(
			user=user,
			project_name='report view',
			summary=json.dumps({'target': {}}),
			totals=json.dumps({'target': {'0': 1}}),
			forward_primer='>target-F\nACGT',
			reverse_primer='>target-R\nTGCA',
		)
		ValidatorPrimerSet.objects.create(
			validator_request=request, primer_name='target-one'
		)
		ValidatorPanel.objects.create(
			validator_request=request, panel='exclusivity', genus='Listeria'
		)
		self.client.force_login(user)

		response = self.client.get(reverse(
			'primer_finder:primer_validator_report',
			kwargs={'validator_pk': request.pk},
		))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['totals'], {'target': {'0': 1}})
		self.assertEqual(response.context['forward'], 'ACGT')
		self.assertEqual(response.context['reverse'], 'TGCA')
		self.assertEqual(response.context['genera'], ['Listeria'])

	def test_check_exit_codes_requires_every_batch_task_to_succeed(self):
		batch_client = mock.Mock()
		passing_task = mock.Mock()
		passing_task.execution_info.exit_code = 0
		failing_task = mock.Mock()
		failing_task.execution_info.exit_code = 1

		batch_client.task.list.side_effect = [[passing_task], [passing_task, failing_task]]

		self.assertTrue(_check_exit_codes(
			batch_client=batch_client, batch_job_name='passing-job'
		))
		self.assertFalse(_check_exit_codes(
			batch_client=batch_client, batch_job_name='failing-job'
		))

	def test_check_tasks_completed_handles_incomplete_and_batch_errors(self):
		batch_client = mock.Mock()
		completed_task = mock.Mock(
			state=primer_tasks.batchmodels.TaskState.completed
		)
		incomplete_task = mock.Mock(state='active')
		batch_client.task.list.side_effect = [
			[completed_task], [incomplete_task], Exception('Batch unavailable')
		]

		self.assertTrue(_check_tasks_completed(
			batch_client=batch_client, batch_job_name='complete-job'
		))
		self.assertFalse(_check_tasks_completed(
			batch_client=batch_client, batch_job_name='active-job'
		))
		self.assertFalse(_check_tasks_completed(
			batch_client=batch_client, batch_job_name='error-job'
		))

	def test_record_request_error_persists_context_on_request_and_task(self):
		user = User.objects.create_user(username='task-error-persistence')
		request = ValidatorRequest.objects.create(user=user, status='Processing')
		task = ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer', status='submitted'
		)

		try:
			raise ValueError('bad report')
		except ValueError as exception:
			error_text = _record_request_error(
				request=request, exception=exception,
				stage='post-processing', task=task,
			)

		request.refresh_from_db()
		task.refresh_from_db()
		self.assertIn('Stage: post-processing', error_text)
		self.assertIn('ValueError: bad report', error_text)
		self.assertEqual(request.status, 'Error')
		self.assertEqual(request.errors, error_text)
		self.assertEqual(task.status, 'error')
		self.assertEqual(task.error, error_text)

	def test_format_task_error_includes_exception_type_and_stage(self):
		error_text = _format_task_error(
			RuntimeError('submission failed'), 'submit task'
		)
		self.assertIn('Stage: submit task', error_text)
		self.assertIn('RuntimeError: submission failed', error_text)

	def test_finalize_validator_request_waits_for_pending_tasks(self):
		user = User.objects.create_user(username='validator-finalize-pending')
		request = ValidatorRequest.objects.create(user=user)
		ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='primer', status='complete'
		)
		ValidatorAnalysisTask.objects.create(
			validator_request=request, task_type='gene', status='submitted'
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_successful_validator_run'
		) as successful, mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_failed_validator_run'
		) as failed:
			_finalize_validator_request(request=request)

		successful.assert_not_called()
		failed.assert_not_called()

	def test_finalize_validator_request_routes_error_and_success(self):
		user = User.objects.create_user(username='validator-finalize-routes')
		error_request = ValidatorRequest.objects.create(user=user)
		ValidatorAnalysisTask.objects.create(
			validator_request=error_request, task_type='primer', status='error'
		)
		success_request = ValidatorRequest.objects.create(user=user)
		ValidatorAnalysisTask.objects.create(
			validator_request=success_request, task_type='primer', status='complete'
		)

		with mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_successful_validator_run'
		) as successful, mock.patch(
			'olc_webportalv2.primer_finder.tasks._handle_failed_validator_run'
		) as failed:
			_finalize_validator_request(request=error_request)
			_finalize_validator_request(request=success_request)

		failed.assert_called_once_with(validator_request=error_request)
		successful.assert_called_once_with(
			batch_job_name=success_request.container_namer(),
			validator_request=success_request,
		)

	def test_display_name_formats_acronyms_and_hyphenated_identifiers(self):
		self.assertEqual(display_name('vtec'), 'STEC')
		self.assertEqual(display_name('escherichia'), 'Escherichia')
		self.assertEqual(display_name('bds-exclusivity'), 'BDS-Exclusivity')
		self.assertEqual(display_name('ncbi-salmonella'), 'NCBI-Salmonella')
		self.assertEqual(display_name(''), '')

	def test_dict_get_returns_value_or_empty_dictionary(self):
		self.assertEqual(dict_get({'result': 1}, 'result'), 1)
		self.assertEqual(dict_get({}, 'missing'), {})

# Create your tests here.
