import argparse
import copy
import importlib
import json
import multiprocessing
import os
import shutil
import time
import traceback

import core.job
import core.session
import core.utils


def before_launch_all_components(context):
    context.mqs['verification statuses'] = multiprocessing.Queue()


def after_decide_verification_task(context):
    context.mqs['verification statuses'].put(context.verification_status)


def after_generate_all_verification_tasks(context):
    context.logger.info('Terminate verification statuses message queue')
    context.mqs['verification statuses'].put(None)


class Core:
    def __init__(self):
        self.exit_code = 0
        self.start_time = 0
        self.default_conf_file = 'core.json'
        self.conf_file = None
        self.conf = {}
        self.is_solving_file = None
        self.is_solving_file_fp = None
        self.logger = None
        self.bridge = {}
        self.version = None
        self.job = None
        self.comp = []
        self.id = '/'
        self.session = None
        self.mqs = {}
        self.uploading_reports_process = None
        self.job_class_components = {
            'Verification of Linux kernel modules': [
                'LKBCE',
                'LKVOG',
                'AVTG',
                'VTG',
            ],
            'Validation on commits in Linux kernel Git repositories': [],
        }
        self.components = []
        self.components_conf = None
        self.callbacks = {}
        self.component_processes = []
        self.data = None

    def main(self):
        try:
            # Use English everywhere below.
            os.environ['LANG'] = 'C'
            os.environ['LC_ALL'] = 'C'
            # Remember approximate time of start to count wall time.
            self.start_time = time.time()
            self.get_conf()
            self.prepare_work_dir()
            self.change_work_dir()
            self.logger = core.utils.get_logger(self.__class__.__name__, self.conf['logging'])
            self.get_version()
            self.job = core.job.Job(self.logger, self.conf['identifier'])
            self.get_comp_desc()
            start_report_file = core.utils.report(self.logger,
                                                  'start',
                                                  {
                                                      'id': self.id,
                                                      'attrs': [{'Klever Core version': self.version}],
                                                      'comp': [
                                                          {attr[attr_shortcut]['name']: attr[attr_shortcut]['value']}
                                                          for attr in self.comp for attr_shortcut in attr
                                                      ]
                                                  })
            self.session = core.session.Session(self.logger, self.conf['Klever Bridge'], self.job.id)
            self.session.decide_job(self.job, start_report_file)
            # TODO: create parallel process to send requests about successful operation to Klever Bridge.
            self.mqs['report files'] = multiprocessing.Queue()
            self.uploading_reports_process = multiprocessing.Process(target=self.send_reports)
            self.uploading_reports_process.start()
            self.job.extract_archive()
            self.job.get_class()
            self.get_components(self.job)
            # Do not read anything from job directory untill job class will be examined (it might be unsupported). This
            # differs from specification that doesn't treat unsupported job classes at all.
            with open(core.utils.find_file_or_dir(self.logger, os.path.curdir, 'job.json'), encoding='ascii') as fp:
                self.job.conf = json.load(fp)
            # TODO: think about implementation in form of classes derived from class Job.
            if self.job.type == 'Verification of Linux kernel modules':
                self.create_components_conf(self.job)
                self.callbacks = core.utils.get_component_callbacks(self.logger, [self.__class__] + self.components,
                                                                    self.components_conf)
                core.utils.invoke_callbacks(self.launch_all_components, (self.id,))
                self.wait_for_components()
            elif self.job.type == 'Validation on commits in Linux kernel Git repositories':
                self.logger.info('Prepare sub-jobs of class "Verification of Linux kernel modules"')
                sub_jobs_common_conf = {}
                if 'Common' in self.job.conf:
                    sub_jobs_common_conf = self.job.conf['Common']
                if 'Sub-jobs' in self.job.conf:
                    for i, sub_job_concrete_conf in enumerate(self.job.conf['Sub-jobs']):
                        sub_job = core.job.Job(self.logger, i)
                        self.job.sub_jobs.append(sub_job)
                        sub_job.type = 'Verification of Linux kernel modules'
                        # Sub-job configuration is based on common sub-jobs configuration.
                        sub_job.conf = copy.deepcopy(sub_jobs_common_conf)
                        core.utils.merge_confs(sub_job.conf, sub_job_concrete_conf)
                self.logger.info('Decide prepared sub-jobs')
                # TODO: looks very like the code above.
                # TODO: create artificial log file for Validator.
                with open('__log', 'w', encoding='ascii') as fp:
                    pass
                self.data = []
                for sub_job in self.job.sub_jobs:
                    commit = sub_job.conf['Linux kernel']['Git repository']['commit']
                    sub_job_id = self.id + str(commit)
                    # TODO: create this auxiliary component reports to allow deciding several sub-jobs. This should be likely done otherwise.
                    core.utils.report(self.logger,
                                      'start',
                                      {
                                          'id': sub_job_id,
                                          'parent id': self.id,
                                          'name': 'Validator',
                                          'attrs': [{'Commit': commit}],
                                      },
                                      self.mqs['report files'],
                                      suffix=' validator {0}'.format(commit))
                    try:
                        os.makedirs(commit)
                        with core.utils.Cd(commit):
                            self.get_components(sub_job)
                            self.create_components_conf(sub_job)
                            self.callbacks = core.utils.get_component_callbacks(self.logger,
                                                                                [self.__class__] + self.components,
                                                                                self.components_conf)
                            core.utils.invoke_callbacks(self.launch_all_components, (sub_job_id,))
                            self.wait_for_components()
                            # TODO: dirty hack to wait for all reports to be uploaded since they may be accidently removed when local source directories use is allowed and next sub-job is decided.
                            while True:
                                time.sleep(1)
                                # Do not wait if reports uploading failed.
                                if self.uploading_reports_process.exitcode:
                                    break
                                if self.mqs['report files'].empty():
                                    time.sleep(3)
                                    break
                        # Do not proceed to other sub-jobs if reports uploading failed.
                        if self.uploading_reports_process.exitcode:
                            break
                    except Exception:
                        if self.mqs:
                            with open('problem desc.txt', 'w', encoding='ascii') as fp:
                                traceback.print_exc(file=fp)

                            if os.path.isfile('problem desc.txt'):
                                core.utils.report(self.logger,
                                                  'unknown',
                                                  {
                                                      'id': sub_job_id + '/unknown',
                                                      'parent id': sub_job_id,
                                                      'problem desc': 'problem desc.txt',
                                                      'files': ['problem desc.txt']
                                                  },
                                                  self.mqs['report files'],
                                                  suffix=' validator {0}'.format(commit))

                        if self.logger:
                            self.logger.exception('Catch exception')
                        else:
                            traceback.print_exc()

                        self.exit_code = 1

                        break
                    finally:
                        if 'verification statuses' in self.mqs:
                            sub_job.conf['obtained verification statuses'] = []
                            while True:
                                verification_status = self.mqs['verification statuses'].get()

                                if verification_status is None:
                                    self.logger.debug('Verification statuses message queue was terminated')
                                    self.mqs['verification statuses'].close()
                                    del self.mqs['verification statuses']
                                    break

                                sub_job.conf['obtained verification statuses'].append(verification_status)

                            # There is no verification statuses when some (sub)component failed prior to VTG strategy
                            # receives some abstract verification tasks.
                            if not sub_job.conf['obtained verification statuses']:
                                sub_job.conf['obtained verification statuses'].append('unknown')

                            self.data.append([sub_job.conf['Linux kernel']['Git repository']['commit'],
                                              sub_job.conf['ideal verdict']] +
                                             sub_job.conf['obtained verification statuses'] +
                                             [sub_job.conf['comment'] if 'comment' in sub_job.conf else None])

                            self.report_validation_results(commit)

                        core.utils.report(self.logger,
                                          'finish',
                                          {
                                              'id': sub_job_id,
                                              'resources': {'wall time': 0, 'CPU time': 0, 'memory size': 0},
                                              'log': '__log',
                                              'files': ['__log']
                                          },
                                          self.mqs['report files'],
                                          suffix=' validator {0}'.format(commit))

                # All validation results were already reported.
                self.data = []
        except Exception:
            if self.mqs:
                with open('problem desc.txt', 'w', encoding='ascii') as fp:
                    traceback.print_exc(file=fp)

                if os.path.isfile('problem desc.txt'):
                    core.utils.report(self.logger,
                                      'unknown',
                                      {
                                          'id': self.id + '/unknown',
                                          'parent id': self.id,
                                          'problem desc': 'problem desc.txt',
                                          'files': ['problem desc.txt']
                                      },
                                      self.mqs['report files'])

            if self.logger:
                self.logger.exception('Catch exception')
            else:
                traceback.print_exc()

            self.exit_code = 1
        finally:
            try:
                for p in self.component_processes:
                    # Do not terminate components that already exitted.
                    if p.is_alive():
                        p.stop()

                if self.mqs:
                    finish_report = {
                        'id': self.id,
                        'resources': core.utils.count_consumed_resources(
                            self.logger,
                            self.start_time),
                        'log': 'log',
                        'files': ['log']
                    }
                    if self.data:
                        finish_report.update({'data': json.dumps(self.data)})
                    core.utils.report(self.logger,
                                      'finish',
                                      finish_report,
                                      self.mqs['report files'])

                    self.logger.info('Terminate report files message queue')
                    self.mqs['report files'].put(None)

                    self.logger.info('Wait for uploading all reports')
                    self.uploading_reports_process.join()
                    # Do not override exit code of main program with the one of auxiliary process uploading reports.
                    if not self.exit_code:
                        self.exit_code = self.uploading_reports_process.exitcode

                if self.session:
                    self.session.sign_out()
            # At least release working directory if cleaning code above will raise some exception.
            finally:
                if self.is_solving_file_fp and not self.is_solving_file_fp.closed:
                    if self.logger:
                        self.logger.info('Release working directory')
                    os.remove(self.is_solving_file)

                if self.logger:
                    self.logger.info('Exit with code "{0}"'.format(self.exit_code))

                return self.exit_code

    def get_conf(self):
        # Get configuration file from command-line options. If it is not specified, then use the default one.
        parser = argparse.ArgumentParser(description='Main script of Klever Core.')
        parser.add_argument('conf file', nargs='?', default=self.default_conf_file,
                            help='configuration file (default: {0})'.format(self.default_conf_file))
        self.conf_file = vars(parser.parse_args())['conf file']

        # Read configuration from file.
        with open(self.conf_file, encoding='ascii') as fp:
            self.conf = json.load(fp)

    def prepare_work_dir(self):
        """
        Clean up and create the working directory. Prevent simultaneous usage of the same working directory.
        """
        # This file exists during Klever Core occupies working directory.
        self.is_solving_file = os.path.join(self.conf['working directory'], 'is solving')

        def check_another_instance():
            if not self.conf['ignore another instances'] and os.path.isfile(self.is_solving_file):
                raise FileExistsError('Another instance of Klever Core occupies working directory "{0}"'.format(
                    self.conf['working directory']))

        check_another_instance()

        # Remove (if exists) and create (if doesn't exist) working directory.
        # Note, that shutil.rmtree() doesn't allow to ignore files as required by specification. So, we have to:
        # - remove the whole working directory (if exists),
        # - create working directory (pass if it is created by another Klever Core),
        # - test one more time whether another Klever Core occupies the same working directory,
        # - occupy working directory.
        shutil.rmtree(self.conf['working directory'], True)

        os.makedirs(self.conf['working directory'], exist_ok=True)

        check_another_instance()

        # Occupy working directory until the end of operation.
        # Yes there may be race condition, but it won't be.
        self.is_solving_file_fp = open(self.is_solving_file, 'w', encoding='ascii')

    def change_work_dir(self):
        # Remember path to configuration file relative to future working directory before changing to it.
        self.conf_file = os.path.relpath(self.conf_file, self.conf['working directory'])

        # Change working directory forever.
        # We can use path for "is solving" file relative to future working directory since exceptions aren't raised when
        # we have relative path but don't change working directory yet.
        self.is_solving_file = os.path.relpath(self.is_solving_file, self.conf['working directory'])
        os.chdir(self.conf['working directory'])

        self.conf['main working directory'] = os.path.abspath(os.path.curdir)

    def get_version(self):
        """
        Get version either as a tag in the Git repository of Klever or from the file created when installing Klever.
        """
        # Git repository directory may be located in parent directory of parent directory.
        git_repo_dir = os.path.join(os.path.dirname(__file__), '../../.git')
        if os.path.isdir(git_repo_dir):
            self.version = core.utils.get_entity_val(self.logger, 'version',
                                                     'git --git-dir {0} describe --always --abbrev=7 --dirty'.format(
                                                         git_repo_dir))
        else:
            # TODO: get version of installed Klever.
            self.version = ''

    def get_comp_desc(self):
        self.logger.info('Get computer description')

        self.comp = [
            {
                entity_name_cmd[0]: {
                    'name': entity_name_cmd[1] if entity_name_cmd[1] else entity_name_cmd[0],
                    'value': core.utils.get_entity_val(self.logger,
                                                       entity_name_cmd[1]
                                                       if entity_name_cmd[1]
                                                       else entity_name_cmd[0],
                                                       entity_name_cmd[2])
                }
            }
            for entity_name_cmd in [
                ['node name', '', 'uname -n'],
                ['CPU model', '', 'cat /proc/cpuinfo | grep -m1 "model name" | sed -r "s/^.*: //"'],
                ['CPUs num', 'number of CPU cores', 'cat /proc/cpuinfo | grep processor | wc -l'],
                ['mem size', 'memory size',
                 'cat /proc/meminfo | grep "MemTotal" | sed -r "s/^.*: *([0-9]+).*/1024 * \\1/" | bc'],
                ['Linux kernel version', '', 'uname -r'],
                ['arch', 'architecture', 'uname -m']

            ]
            ]

    def send_reports(self):
        try:
            while True:
                # TODO: replace MQ with "reports and report files archives".
                report_and_report_files_archive = self.mqs['report files'].get()

                if report_and_report_files_archive is None:
                    self.logger.debug('Report files message queue was terminated')
                    # Note that this and all other closing of message queues aren't strictly necessary and everything
                    # will work without them as well, but this potentially can save some memory since closing explicitly
                    # notifies that corresponding message queue won't be used any more and its memory could be freed.
                    self.mqs['report files'].close()
                    break

                report_file = report_and_report_files_archive['report file']
                report_files_archive = report_and_report_files_archive.get('report files archive')

                self.logger.debug('Upload report file "{0}"{1}'
                                  .format(report_file,
                                          ' with report files archive "{0}"'.format(report_files_archive)
                                          if report_files_archive
                                          else ''))

                self.session.upload_report(report_file, report_files_archive)
        except Exception as e:
            # If we can't send reports to Klever Bridge by some reason we can just silently die.
            self.logger.exception('Catch exception when sending reports to Klever Bridge')
            exit(1)

    def get_components(self, job):
        self.logger.info('Get components necessary to solve job of class "{0}"'.format(job.type))

        if job.type not in self.job_class_components:
            raise KeyError('Job class "{0}" is not supported'.format(job.type))

        self.components = [getattr(importlib.import_module('.{0}'.format(component.lower()), 'core'), component) for
                           component in self.job_class_components[job.type]]

        self.logger.debug(
            'Components to be launched: "{0}"'.format(', '.join([component.__name__ for component in self.components])))

    def create_components_conf(self, job):
        """
        Create configuration to be used by all Klever Core components.
        """
        self.logger.info('Create components configuration')

        # Components configuration is based on job configuration.
        self.components_conf = job.conf

        # Convert list of primitive dictionaries to one dictionary to simplify code below.
        comp = {}
        for attr in self.comp:
            comp.update(attr)

        # Add complete Klever Core configuration itself to components configuration since almost all its attributes will
        # be used somewhere in components.
        self.components_conf.update(self.conf)

        self.components_conf.update({'sys': {attr: comp[attr]['value'] for attr in ('CPUs num', 'mem size', 'arch')}})

        if self.conf['keep intermediate files']:
            if os.path.isfile('components conf.json'):
                raise FileExistsError('Components configuration file "components conf.json" already exists')
            self.logger.debug('Create components configuration file "components conf.json"')
            with open('components conf.json', 'w', encoding='ascii') as fp:
                json.dump(self.components_conf, fp, sort_keys=True, indent=4)

    def launch_all_components(self, parent_id):
        self.logger.info('Launch all components')

        for component in self.components:
            p = component(self.components_conf, self.logger, parent_id, self.callbacks, self.mqs,
                          separate_from_parent=True)
            p.start()
            self.component_processes.append(p)

    def wait_for_components(self):
        self.logger.info('Wait for components')

        # Every second check whether some component died. Otherwise even if some non-first component will die we
        # will wait for all components that preceed that failed component prior to notice that something went wrong.
        # Treat process that upload reports as component that may fail.
        while True:
            # The number of components that are still operating.
            operating_components_num = 0

            for p in self.component_processes:
                p.join(1.0 / len(self.component_processes))
                operating_components_num += p.is_alive()

            if not operating_components_num or self.uploading_reports_process.exitcode:
                break

        # Clean up this list to properly decide other sub-jobs.
        if not self.uploading_reports_process.exitcode:
            self.component_processes = []

    def report_validation_results(self, commit):
        self.logger.info('Relate validation results on commits before and after corresponding bug fixes if so')
        validation_results = []
        validation_results_before_bug_fixes = []
        validation_results_after_bug_fixes = []
        for validation_res in self.data:
            # Corresponds to validation result before bug fix.
            if validation_res[1] == 'unsafe':
                validation_results_before_bug_fixes.append(validation_res)
            # Corresponds to validation result after bug fix.
            elif validation_res[1] == 'safe':
                validation_results_after_bug_fixes.append(validation_res)
            else:
                raise ValueError(
                    'Ideal verdict is "{0}" (either "safe" or "unsafe" is expected)'.format(validation_res[1]))
        for commit1, ideal_verdict1, verification_status1, comment1 in validation_results_before_bug_fixes:
            found_validation_res_after_bug_fix = False
            for commit2, ideal_verdict2, verification_status2, comment2 in validation_results_after_bug_fixes:
                # Commit hash before/after corresponding bug fix is considered to be "hash~"/"hash" or v.v.
                if commit1 == commit2 + '~' or commit2 == commit1 + '~':
                    found_validation_res_after_bug_fix = True
                    break
            validation_res_msg = 'Verification status of bug "{0}" before fix is "{1}"{2}'.format(
                commit1, verification_status1, ' ("{0}")'.format(comment1) if comment1 else '')
            # At least save validation result before bug fix.
            if not found_validation_res_after_bug_fix:
                self.logger.warning('Could not find validation result after fix of bug "{0}"'.format(commit1))
                validation_results.append([commit1, verification_status1, comment1, None, None])
            else:
                validation_res_msg += ', after fix is "{0}"{1}'.format(verification_status2,
                                                                       ' ("{0}")'.format(comment2) if comment2 else '')
                validation_results.append([commit1, verification_status1, comment1, verification_status2, comment2])
            self.logger.info(validation_res_msg)

        core.utils.report(self.logger,
                          'data',
                          {
                              'id': self.id,
                              'data': json.dumps(validation_results)
                          },
                          self.mqs['report files'],
                          suffix=' {0}'.format(commit))
