#!/usr/bin/python3

import copy
import json
import multiprocessing
import os
import random

import psi.components
import psi.utils


def before_launch_all_components(context):
    context.mqs['AVTG common prj attrs'] = multiprocessing.Queue()
    context.mqs['verification obj descs'] = multiprocessing.Queue()


def after_extract_common_prj_attrs(context):
    context.mqs['AVTG common prj attrs'].put(context.common_prj_attrs)


def after_generate_verification_obj_desc(context):
    # We need to copy verification object description since it may be accidently overwritten by LKVOG.
    context.mqs['verification obj descs'].put(copy.deepcopy(context.verification_obj_desc))


def after_generate_all_verification_obj_descs(context):
    context.logger.info('Terminate verification object descriptions message queue')
    context.mqs['verification obj descs'].put(None)


# TODO: get callbacks of plugins.


class AVTG(psi.components.Component):
    def generate_abstract_verification_tasks(self):
        self.common_prj_attrs = {}
        self.extract_common_prj_attrs()
        psi.utils.report(self.logger,
                         'attrs',
                         {'id': self.name,
                          'attrs': self.common_prj_attrs},
                         self.mqs['report files'],
                         self.conf['root id'])
        self.rule_spec_descs = _extract_rule_spec_descs(self.conf, self.logger)
        psi.utils.invoke_callbacks(self.generate_all_abstract_verification_task_descs)

        # TODO: delete following stub code after all.
        # Start and finish AVTG plugins.
        for i, verification_obj in enumerate(('drivers/usb/core/usbcore.ko', 'drivers/usb/usb-commmon.ko')):
            for j, rule_spec in enumerate(('mutex', 'spin lock')):
                for plugin in ('DEG', 'RI'):
                    # Surprise! RI11 isn't started since DEG11 is going to fail. Keep in touch!
                    if i == 1 and j == 1 and plugin == 'RI':
                        continue

                    id = '{0}/{1}/{2}'.format(verification_obj, rule_spec, plugin)
                    plugin_work_dir = '{0}{1}{2}'.format(plugin, i, j)

                    os.makedirs(plugin_work_dir)
                    os.chdir(plugin_work_dir)

                    psi.utils.report(self.logger,
                                     'start',
                                     {'id': id,
                                      'attrs': [{'verification object': verification_obj},
                                                {'rule specification': rule_spec}],
                                      'name': plugin,
                                      'parent id': 'AVTG'},
                                     self.mqs['report files'],
                                     self.conf['root id'])

                    # As promised DEG11 fails.
                    if i == 1 and j == 1 and plugin == 'DEG':
                        psi.utils.report(self.logger,
                                         'unknown',
                                         {'id': 'unknown',
                                          'parent id': id,
                                          'problem desc': 'Fatal error!'},
                                         self.mqs['report files'],
                                         self.conf['root id'])

                    psi.utils.report(self.logger,
                                     'finish',
                                     {'id': id,
                                      'resources': {'wall time': random.randint(0, 10000),
                                                    'CPU time': random.randint(0, 10000),
                                                    'max mem size': random.randint(0, 1000000000)},
                                      'log': '',
                                      'data': ''},
                                     self.mqs['report files'],
                                     self.conf['root id'])

                    os.chdir(os.pardir)

    main = generate_abstract_verification_tasks

    def extract_common_prj_attrs(self):
        self.logger.info('Extract common project atributes')

        self.common_prj_attrs = self.mqs['AVTG common prj attrs'].get()

        self.mqs['AVTG common prj attrs'].close()

    def generate_all_abstract_verification_task_descs(self):
        self.logger.info('Generate all abstract verification task decriptions')

        while True:
            verification_obj_desc = self.mqs['verification obj descs'].get()

            if verification_obj_desc is None:
                self.logger.debug('Verification object descriptions message queue was terminated')
                self.mqs['verification obj descs'].close()
                break

            # TODO: specification requires to do this in parallel...
            for rule_spec_desc in self.rule_spec_descs:
                self.generate_abstact_verification_task_desc(verification_obj_desc, rule_spec_desc)

    def generate_abstact_verification_task_desc(self, verification_obj_desc, rule_spec_desc):
        self.logger.info(
            'Generate abstract verification task description for {0}'.format(
                'verification object "{0}" and rule specification "{1}"'.format(
                    verification_obj_desc['id'], rule_spec_desc['id'])))

        # plugins_work_dir = os.path.relpath(
        #     os.path.join(self.conf['root id'], '{0}.task'.format(verification_obj_desc['id'])))
        # os.makedirs(plugins_work_dir)
        # self.logger.debug('Plugins working directory is "{0}"'.format(plugins_work_dir))
        #
        # for plugin_desc in rule_spec_desc['plugins']:
        #     pass

        # TODO: generate abstract verification task description!


def _extract_rule_spec_descs(conf, logger):
    logger.info('Extract rule specificaction decriptions')

    # Read rule specification descriprions DB.
    with open(psi.utils.find_file_or_dir(logger, conf['root id'], conf['rule specifications DB'])) as fp:
        descs = json.load(fp)

    rule_spec_descs = []

    for rule_spec_id in conf['rule specifications']:
        logger.info('Extract description for rule specification "{0}"'.format(rule_spec_id))

        # Get raw rule specification description.
        if 'rule specifications' not in descs or rule_spec_id not in descs['rule specifications']:
            is_alias_found = False
            for potential_rule_spec_id, potential_rule_spec_desc in descs['rule specifications'].items():
                if 'aliases' in potential_rule_spec_desc:
                    for alias in potential_rule_spec_desc['aliases']:
                        if rule_spec_id == alias:
                            is_alias_found = True
                            logger.debug(
                                'Rule specification "{0}" was found by alias "{1}"'.format(potential_rule_spec_id,
                                                                                           rule_spec_id))
                            # Use true rule specification ID rather than its alias further.
                            rule_spec_id = potential_rule_spec_id
                            break
                if is_alias_found:
                    rule_spec_desc = potential_rule_spec_desc
                    break

            if not is_alias_found:
                raise ValueError(
                    'Specified rule specification "{0}" could not be found in rule specifications DB'.format(
                        rule_spec_id))
        else:
            rule_spec_desc = descs['rule specifications'][rule_spec_id]

        # Get rid of useless information.
        for attr in ('aliases', 'desc'):
            if attr in rule_spec_desc:
                del (rule_spec_desc[attr])

        # Get rule specification template which it is based on.
        if 'template' not in rule_spec_desc:
            raise ValueError(
                'Rule specification "{0}" has not mandatory attribute "template"'.format(rule_spec_id))
        tmpl_id = rule_spec_desc['template']
        # This information won't be used any more.
        del (rule_spec_desc['template'])
        logger.debug('Rule specification "{0}" template is "{1}"'.format(rule_spec_id, tmpl_id))
        if 'templates' not in descs or tmpl_id not in descs['templates']:
            raise ValueError(
                'Template "{0}" specified in rule specification "{0}" could not be found in rule specifications DB'.format(
                    tmpl_id, rule_spec_id))
        tmpl_desc = descs['templates'][tmpl_id]

        # Get raw template plugins.
        if 'plugins' not in tmpl_desc:
            raise ValueError(
                'Template "{0}" has not mandatory attribute "plugins"'.format(tmpl_id))
        # Copy plugin descriptions since we will overwrite them while the same template can be used by different rule
        # specifications
        plugin_descs = copy.deepcopy(tmpl_desc['plugins'])
        for idx, plugin_desc in enumerate(plugin_descs):
            if 'name' not in plugin_desc:
                raise ValueError(
                    'Template "{0}" plugin "{1}" has not mandatory attribute "name"'.format(tmpl_id, idx))
        logger.debug(
            'Template "{0}" plugins are "{1}"'.format(tmpl_id, [plugin_desc['name'] for plugin_desc in plugin_descs]))

        # Add plugin options specific for rule specification.
        rule_spec_plugin_names = []
        for attr in rule_spec_desc:
            # Names of all other attributes are considered as plugin names, values - as corresponding plugin options.
            if attr not in ('aliases', 'desc', 'bug kinds', 'template'):
                plugin_name = attr
                rule_spec_plugin_names.append(plugin_name)
                is_plugin_specified = False

                for plugin_desc in plugin_descs:
                    if plugin_name == plugin_desc['name']:
                        is_plugin_specified = True
                        if 'opts' not in plugin_desc['name']:
                            plugin_desc['opts'] = {}
                        plugin_desc['opts'].update(rule_spec_desc[plugin_name])
                        logger.debug(
                            'Plugin "{0}" options specific for rule specification "{1}" are "{2}"'.format(plugin_name,
                                                                                                          rule_spec_id,
                                                                                                          rule_spec_desc[
                                                                                                              plugin_name]))
                        break

                if not is_plugin_specified:
                    raise ValueError(
                        'Rule specification "{0}" plugin "{1}" is not specified in template "{2}"'.format(
                            rule_spec_id, plugin_name, tmpl_id))
        # We don't need to keep plugin options specific for rule specification in such the form any more.
        for plugin_name in rule_spec_plugin_names:
            del (rule_spec_desc[plugin_name])
        rule_spec_desc['plugins'] = plugin_descs

        rule_spec_descs.append({'id': rule_spec_id, 'desc': rule_spec_desc})

    return rule_spec_descs
