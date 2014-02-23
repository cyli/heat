# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections

from heat.common import exception
from heat.engine import function
from heat.engine import template
from heat.engine import parameters
from heat.engine import constraints as constr
from heat.openstack.common.gettextutils import _
from heat.openstack.common import log as logging


logger = logging.getLogger(__name__)

PARAM_CONSTRAINTS = (
    CONSTRAINTS, DESCRIPTION, LENGTH, RANGE, MIN, MAX,
    ALLOWED_VALUES, ALLOWED_PATTERN, CUSTOM_CONSTRAINT,
) = (
    'constraints', 'description', 'length', 'range', 'min', 'max',
    'allowed_values', 'allowed_pattern', 'custom_constraint',
)


def snake_to_camel(name):
    return ''.join([t.capitalize() for t in name.split('_')])


class HOTemplate(template.Template):
    """
    A Heat Orchestration Template format stack template.
    """

    SECTIONS = (VERSION, DESCRIPTION, PARAMETER_GROUPS, PARAMETERS,
                RESOURCES, OUTPUTS, UNDEFINED) = \
               ('heat_template_version', 'description', 'parameter_groups',
                'parameters', 'resources', 'outputs', '__undefined__')

    SECTIONS_NO_DIRECT_ACCESS = set([PARAMETERS])

    _CFN_TO_HOT_SECTIONS = {template.Template.VERSION: VERSION,
                            template.Template.DESCRIPTION: DESCRIPTION,
                            template.Template.PARAMETERS: PARAMETERS,
                            template.Template.MAPPINGS: UNDEFINED,
                            template.Template.RESOURCES: RESOURCES,
                            template.Template.OUTPUTS: OUTPUTS}

    def __getitem__(self, section):
        """"Get the relevant section in the template."""
        #first translate from CFN into HOT terminology if necessary
        section = HOTemplate._translate(section,
                                        self._CFN_TO_HOT_SECTIONS, section)

        if section not in self.SECTIONS:
            raise KeyError(_('"%s" is not a valid template section') % section)
        if section in self.SECTIONS_NO_DIRECT_ACCESS:
            raise KeyError(
                _('Section %s can not be accessed directly.') % section)

        if section == self.VERSION:
            return self.t[section]

        if section == self.UNDEFINED:
            return {}

        if section == self.DESCRIPTION:
            default = 'No description'
        else:
            default = {}

        the_section = self.t.get(section, default)

        # In some cases (e.g. parameters), also translate each entry of
        # a section into CFN format (case, naming, etc) so the rest of the
        # engine can cope with it.
        # This is a shortcut for now and might be changed in the future.

        if section == self.RESOURCES:
            return self._translate_resources(the_section)

        if section == self.OUTPUTS:
            return self._translate_outputs(the_section)

        return the_section

    @staticmethod
    def _translate(value, mapping, default=None):
        if value in mapping:
            return mapping[value]

        return default

    def _translate_resources(self, resources):
        """Get the resources of the template translated into CFN format."""
        HOT_TO_CFN_ATTRS = {'type': 'Type',
                            'properties': 'Properties'}

        cfn_resources = {}

        for resource_name, attrs in resources.iteritems():
            cfn_resource = {}

            for attr, attr_value in attrs.iteritems():
                cfn_attr = self._translate(attr, HOT_TO_CFN_ATTRS, attr)
                cfn_resource[cfn_attr] = attr_value

            cfn_resources[resource_name] = cfn_resource

        return cfn_resources

    def _translate_outputs(self, outputs):
        """Get the outputs of the template translated into CFN format."""
        HOT_TO_CFN_ATTRS = {'description': 'Description',
                            'value': 'Value'}

        cfn_outputs = {}

        for output_name, attrs in outputs.iteritems():
            cfn_output = {}

            for attr, attr_value in attrs.iteritems():
                cfn_attr = self._translate(attr, HOT_TO_CFN_ATTRS, attr)
                cfn_output[cfn_attr] = attr_value

            cfn_outputs[output_name] = cfn_output

        return cfn_outputs

    def param_schemata(self):
        params = self.t.get(self.PARAMETERS, {}).iteritems()
        return dict((name, HOTParamSchema.from_dict(schema))
                    for name, schema in params)

    def parameters(self, stack_identifier, user_params, validate_value=True,
                   context=None):
        return HOTParameters(stack_identifier, self, user_params=user_params,
                             validate_value=validate_value, context=context)

    def functions(self):
        return {
            'Fn::FindInMap': template.FindInMap,
            'Fn::GetAZs': template.GetAZs,
            'get_param': GetParam,
            'get_resource': template.ResourceRef,
            'Ref': template.Ref,
            'get_attr': GetAtt,
            'Fn::Select': template.Select,
            'Fn::Join': template.Join,
            'Fn::Split': template.Split,
            'str_replace': Replace,
            'Fn::Replace': template.Replace,
            'Fn::Base64': template.Base64,
            'Fn::MemberListToMap': template.MemberListToMap,
            'Fn::ResourceFacade': template.ResourceFacade,
            'get_file': GetFile,
        }


class GetParam(function.Function):
    '''
    A function for resolving parameter references.

    Takes the form::

        get_param: <param_name>

    or::

        get_param:
          - <param_name>
          - <path1>
          - ...
    '''

    def result(self):
        args = function.resolve(self.args)

        if not args:
            raise ValueError(_('Function "%s" must have arguments') %
                             self.fn_name)

        if isinstance(args, basestring):
            param_name = args
            path_components = []
        elif isinstance(args, collections.Sequence):
            param_name = args[0]
            path_components = args[1:]
        else:
            raise TypeError(_('Argument to "%s" must be string or list') %
                            self.fn_name)

        if not isinstance(param_name, basestring):
            raise TypeError(_('Parameter name in "%s" must be string') %
                            self.fn_name)

        try:
            parameter = self.stack.parameters[param_name]
        except KeyError:
            raise exception.UserParameterMissing(key=param_name)

        def get_path_component(collection, key):
            if not isinstance(collection, (collections.Mapping,
                                           collections.Sequence)):
                raise TypeError(_('"%s" can\'t traverse path') % self.fn_name)

            if not isinstance(key, (basestring, int)):
                raise TypeError(_('Path components in "%s" '
                                  'must be strings') % self.fn_name)

            return collection[key]

        try:
            return reduce(get_path_component, path_components, parameter)
        except (KeyError, IndexError, TypeError):
            return ''


class GetAtt(template.GetAtt):
    '''
    A function for resolving resource attributes.

    Takes the form::

        get_attr:
          - <resource_name>
          - <attribute_name>
          - <path1>
          - ...
    '''

    def _parse_args(self):
        if (not isinstance(self.args, collections.Sequence) or
                isinstance(self.args, basestring)):
            raise TypeError(_('Argument to "%s" must be a list') %
                            self.fn_name)

        if len(self.args) < 2:
            raise ValueError(_('Arguments to "%s" must be of the form '
                               '[resource_name, attribute, (path), ...]') %
                             self.fn_name)

        self._path_components = self.args[2:]

        return tuple(self.args[:2])

    def result(self):
        attribute = super(GetAtt, self).result()
        if attribute is None:
            return ''

        path_components = function.resolve(self._path_components)

        def get_path_component(collection, key):
            if not isinstance(collection, (collections.Mapping,
                                           collections.Sequence)):
                raise TypeError(_('"%s" can\'t traverse path') % self.fn_name)

            if not isinstance(key, (basestring, int)):
                raise TypeError(_('Path components in "%s" '
                                  'must be strings') % self.fn_name)

            return collection[key]

        try:
            return reduce(get_path_component, path_components, attribute)
        except (KeyError, IndexError, TypeError):
            return ''


class Replace(template.Replace):
    '''
    A function for performing string substitutions.

    Takes the form::

        str_replace:
          template: <key_1> <key_2>
          params:
            <key_1>: <value_1>
            <key_2>: <value_2>
            ...

    And resolves to::

        "<value_1> <value_2>"

    This is implemented using Python's str.replace on each key. The order in
    which replacements are performed is undefined.
    '''

    def _parse_args(self):
        if not isinstance(self.args, collections.Mapping):
            raise TypeError(_('Arguments to "%s" must be a map') %
                            self.fn_name)

        try:
            mapping = self.args['params']
            string = self.args['template']
        except (KeyError, TypeError):
            example = ('''str_replace:
              template: This is var1 template var2
              params:
                var1: a
                var2: string''')
            raise KeyError(_('"str_replace" syntax should be %s') %
                           example)
        else:
            return mapping, string


class GetFile(function.Function):
    """
    A function for including a file inline.

    Takes the form::

        get_file: <file_key>

    And resolves to the content stored in the files dictionary under the given
    key.
    """

    def result(self):
        args = function.resolve(self.args)
        if not (isinstance(args, basestring)):
            raise TypeError(_('Argument to "%s" must be a string') %
                            self.fn_name)

        f = self.stack.t.files.get(args)
        if f is None:
            fmt_data = {'fn_name': self.fn_name,
                        'file_key': args}
            raise ValueError(_('No content found in the "files" section for '
                               '%(fn_name)s path: %(file_key)s') % fmt_data)
        return f


class HOTParamSchema(parameters.Schema):
    """HOT parameter schema."""

    KEYS = (
        TYPE, DESCRIPTION, DEFAULT, SCHEMA, CONSTRAINTS,
        HIDDEN, LABEL
    ) = (
        'type', 'description', 'default', 'schema', 'constraints',
        'hidden', 'label'
    )

    # For Parameters the type name for Schema.LIST is comma_delimited_list
    # and the type name for Schema.MAP is json
    TYPES = (
        STRING, NUMBER, LIST, MAP,
    ) = (
        'string', 'number', 'comma_delimited_list', 'json',
    )

    @classmethod
    def from_dict(cls, schema_dict):
        """
        Return a Parameter Schema object from a legacy schema dictionary.
        """

        def constraints():
            constraints = schema_dict.get(CONSTRAINTS)
            if constraints is None:
                return

            for constraint in constraints:
                desc = constraint.get(DESCRIPTION)
                if RANGE in constraint:
                    cdef = constraint.get(RANGE)
                    yield constr.Range(parameters.Schema.get_num(MIN, cdef),
                                       parameters.Schema.get_num(MAX, cdef),
                                       desc)
                if LENGTH in constraint:
                    cdef = constraint.get(LENGTH)
                    yield constr.Length(parameters.Schema.get_num(MIN, cdef),
                                        parameters.Schema.get_num(MAX, cdef),
                                        desc)
                if ALLOWED_VALUES in constraint:
                    cdef = constraint.get(ALLOWED_VALUES)
                    yield constr.AllowedValues(cdef, desc)
                if ALLOWED_PATTERN in constraint:
                    cdef = constraint.get(ALLOWED_PATTERN)
                    yield constr.AllowedPattern(cdef, desc)
                if CUSTOM_CONSTRAINT in constraint:
                    cdef = constraint.get(CUSTOM_CONSTRAINT)
                    yield constr.CustomConstraint(cdef, desc)

        # make update_allowed true by default on TemplateResources
        # as the template should deal with this.
        return cls(schema_dict[cls.TYPE],
                   description=schema_dict.get(HOTParamSchema.DESCRIPTION),
                   default=schema_dict.get(HOTParamSchema.DEFAULT),
                   constraints=list(constraints()),
                   hidden=schema_dict.get(HOTParamSchema.HIDDEN, False))


class HOTParameters(parameters.Parameters):
    PSEUDO_PARAMETERS = (
        PARAM_STACK_ID, PARAM_STACK_NAME, PARAM_REGION
    ) = (
        'OS::stack_id', 'OS::stack_name', 'OS::region'
    )

    def set_stack_id(self, stack_identifier):
        '''
        Set the StackId pseudo parameter value
        '''
        if stack_identifier is not None:
            self.params[self.PARAM_STACK_ID].schema.set_default(
                stack_identifier.stack_id)
        else:
            raise exception.InvalidStackIdentifier()

    def _pseudo_parameters(self, stack_identifier):
        stack_id = getattr(stack_identifier, 'stack_id', '')
        stack_name = getattr(stack_identifier, 'stack_name', '')

        yield parameters.Parameter(
            self.PARAM_STACK_ID,
            parameters.Schema(parameters.Schema.STRING, _('Stack ID'),
                              default=str(stack_id)))
        if stack_name:
            yield parameters.Parameter(
                self.PARAM_STACK_NAME,
                parameters.Schema(parameters.Schema.STRING, _('Stack Name'),
                                  default=stack_name))