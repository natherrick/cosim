# -*- coding: utf-8 -*-
#
# nest_codegenerator.py
#
# This file is part of NEST.
#
# Copyright (C) 2004 The NEST Initiative
#
# NEST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# NEST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with NEST.  If not, see <http://www.gnu.org/licenses/>.

from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import copy
import json
import datetime
import os
import re
import sympy
import sympy.parsing
from jinja2 import Environment, FileSystemLoader, TemplateRuntimeError
from odetoolbox import analysis

import pynestml

from pynestml.codegeneration.ast_transformers import add_declarations_to_internals, add_declaration_to_state_block, declaration_in_state_block, is_delta_kernel, replace_rhs_variables, construct_kernel_X_spike_buf_name, get_expr_from_kernel_var, to_ode_toolbox_name, to_ode_toolbox_processed_name, get_kernel_var_order_from_ode_toolbox_result, get_initial_value_from_ode_toolbox_result, variable_in_kernels, is_ode_variable, variable_in_solver, recursive_dependent_variables_search, get_input_port_by_name, collect_variable_names_in_expression, get_eq_declarations_from_block
from pynestml.codegeneration.codegenerator import CodeGenerator
from pynestml.codegeneration.expressions_pretty_printer import ExpressionsPrettyPrinter
from pynestml.codegeneration.gsl_names_converter import GSLNamesConverter
from pynestml.codegeneration.gsl_reference_converter import GSLReferenceConverter
from pynestml.codegeneration.ode_toolbox_reference_converter import ODEToolboxReferenceConverter
from pynestml.codegeneration.unitless_expression_printer import UnitlessExpressionPrinter
from pynestml.codegeneration.nest_assignments_helper import NestAssignmentsHelper
from pynestml.codegeneration.nest_declarations_helper import NestDeclarationsHelper
from pynestml.codegeneration.nest_names_converter import NestNamesConverter
from pynestml.codegeneration.nest_printer import NestPrinter
from pynestml.codegeneration.nest_reference_converter import NESTReferenceConverter
from pynestml.frontend.frontend_configuration import FrontendConfiguration
from pynestml.meta_model.ast_declaration import ASTDeclaration
from pynestml.meta_model.ast_external_variable import ASTExternalVariable
from pynestml.meta_model.ast_return_stmt import ASTReturnStmt
from pynestml.meta_model.ast_assignment import ASTAssignment
from pynestml.meta_model.ast_equations_block import ASTEquationsBlock
from pynestml.meta_model.ast_input_port import ASTInputPort
from pynestml.meta_model.ast_inline_expression import ASTInlineExpression
from pynestml.meta_model.ast_kernel import ASTKernel
from pynestml.meta_model.ast_node import ASTNode
from pynestml.meta_model.ast_neuron import ASTNeuron
from pynestml.meta_model.ast_synapse import ASTSynapse
from pynestml.meta_model.ast_kernel import ASTKernel
from pynestml.meta_model.ast_ode_equation import ASTOdeEquation
from pynestml.meta_model.ast_simple_expression import ASTSimpleExpression
from pynestml.meta_model.ast_stmt import ASTStmt
from pynestml.meta_model.ast_variable import ASTVariable
from pynestml.symbol_table.symbol_table import SymbolTable
from pynestml.symbols.symbol import SymbolKind
from pynestml.symbols.variable_symbol import BlockType
from pynestml.utils.ast_utils import ASTUtils
from pynestml.utils.logger import Logger
from pynestml.utils.logger import LoggingLevel
from pynestml.utils.messages import Messages
from pynestml.utils.model_parser import ModelParser
from pynestml.utils.ode_transformer import OdeTransformer
from pynestml.visitors.ast_symbol_table_visitor import ASTSymbolTableVisitor
from pynestml.symbol_table.symbol_table import SymbolTable
from pynestml.symbols.variable_symbol import BlockType
from pynestml.meta_model.ast_simple_expression import ASTSimpleExpression
from pynestml.meta_model.ast_expression import ASTExpression
from pynestml.visitors.ast_higher_order_visitor import ASTHigherOrderVisitor
from pynestml.visitors.ast_visitor import ASTVisitor
from pynestml.visitors.ast_random_number_generator_visitor import ASTRandomNumberGeneratorVisitor

# 
# def get_post_port_by_name(input_block, port_name):
#     for port in input_block.get_input_ports():
#         if port.get_name() == port_name and port.is_post():
#             return port
#     return None


class NESTCodeGenerator(CodeGenerator):
    """
    Code generator for a C++ NEST extension module.

    Options:
    - **neuron_parent_class**: The C++ class from which the generated NESTML neuron class inherits. Examples: ``"ArchivingNode"``, ``"StructuralPlasticityNode"``. Default: ``"ArchivingNode"``.
    - **neuron_parent_class_include**: The C++ header filename to include that contains **neuron_parent_class**. Default: ``"archiving_node.h"``.
    - **neuron_synapse_pairs**: List of pairs of (neuron, synapse) model names.
    - **preserve_expressions**: Set to True, or a list of strings corresponding to individual variable names, to disable internal rewriting of expressions, and return same output as input expression where possible. Only applies to variables specified as first-order differential equations. (This parameter is passed to ODE-toolbox.)
    - **simplify_expression**: For all expressions ``expr`` that are rewritten by ODE-toolbox: the contents of this parameter string are ``eval()``ed in Python to obtain the final output expression. Override for custom expression simplification steps. Example: ``sympy.simplify(expr)``. Default: ``"sympy.logcombine(sympy.powsimp(sympy.expand(expr)))"``. (This parameter is passed to ODE-toolbox.)
    """

    _default_options = {
        "neuron_parent_class": "ArchivingNode",
        "neuron_parent_class_include": "archiving_node.h",
        "neuron_synapse_pairs": [],
        "preserve_expressions": False,
        "simplify_expression": "sympy.logcombine(sympy.powsimp(sympy.expand(expr)))"
    }

    _variable_matching_template = r'(\b)({})(\b)'

    def __init__(self, options: Optional[Mapping[str, Any]]=None):
        super().__init__("NEST", options)
        self.analytic_solver = {}
        self.numeric_solver = {}
        self.non_equations_state_variables = {}   # those state variables not defined as an ODE in the equations block
        self._setup_template_env()

    def _setup_template_env(self):
        """setup the Jinja2 template environment"""

        def raise_helper(msg):
            raise TemplateRuntimeError(msg)

        env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'resources_nest')))
        env.globals['raise'] = raise_helper
        env.globals["is_delta_kernel"] = is_delta_kernel
        setup_env = Environment(loader=FileSystemLoader(os.path.join(
            os.path.dirname(__file__), 'resources_nest', 'setup')))
        setup_env.globals['raise'] = raise_helper
        # setup the cmake template
        self._template_cmakelists = setup_env.get_template('CMakeLists.jinja2')
        # setup the module class template
        self._template_module_class = env.get_template('ModuleClass.jinja2')
        # setup the NEST module template
        self._template_module_header = env.get_template('ModuleHeader.jinja2')
        # setup the SLI_Init file
        self._template_sli_init = setup_env.get_template('SLI_Init.jinja2')
        # setup the neuron header template
        self._template_neuron_h_file = env.get_template('NeuronHeader.jinja2')
        # setup the neuron implementation template
        self._template_neuron_cpp_file = env.get_template('NeuronClass.jinja2')
        # setup the synapse header template
        self._template_synapse_h_file = env.get_template('SynapseHeader.jinja2')

        self._printer = ExpressionsPrettyPrinter()


    def is_post_port(self, port_name: str, neuron_name: str, synapse_name: str):
        """Check if a port by the given name is specified as connecting to the postsynaptic neuron. Only makes sense for synapses."""
        if not "neuron_synapse_pairs" in self._options.keys():
            return False

        for neuron_synapse_pair in self._options["neuron_synapse_pairs"]:
            if not (neuron_synapse_pair["neuron"] + FrontendConfiguration.suffix == neuron_name
                    and neuron_synapse_pair["synapse"] + FrontendConfiguration.suffix == synapse_name):
                continue

            if not "post_ports" in neuron_synapse_pair.keys():
                return False

            post_ports = neuron_synapse_pair["post_ports"]
            if not isinstance(post_ports, list):
                # only one port name given, not a list
                return port_name == post_ports

            for post_port in post_ports:
                if type(post_port) is not str and len(post_port) == 2:  # (syn_port_name, neuron_port_name) tuple
                    post_port = post_port[0]
                if type(post_port) is not str and len(post_port) == 1:  # (syn_port_name)
                    return post_port[0] == port_name
                if port_name == post_port:
                    return True
        return False
        
    def get_all_variables(self, node: ASTNode) -> List[str]:
        """Make a list of all variable symbol names that are in ``node``"""
        class ASTVariablesFinderVisitor(ASTVisitor):
            _variables = []

            def __init__(self):
                super(ASTVariablesFinderVisitor, self).__init__()


            def visit_declaration(self, node):
                symbol = node.get_scope().resolve_to_symbol(node.get_variables()[0].get_complete_name(),
                                                            SymbolKind.VARIABLE)
                if symbol is None:
                    code, message = Messages.get_variable_not_defined(node.get_variable().get_complete_name())
                    Logger.log_message(code=code, message=message, error_position=node.get_source_position(),
                                       log_level=LoggingLevel.ERROR, astnode=node)
                    return

                self._variables.append(symbol)

        if node is None:
            return []

        visitor = ASTVariablesFinderVisitor()
        node.accept(visitor)
        all_variables = [v.name for v in visitor._variables]
        return all_variables


    def get_all_Variables_used_in_convolutions(self, node: ASTNode, parent_node: ASTNode) -> List[str]:
        """Make a list of all variable symbol names that are in ``node`` and used in a convolution"""
        class ASTAllVariablesUsedInConvolutionVisitor(ASTVisitor):
            _variables = []
            parent_node = None

            def __init__(self, node, parent_node):
                super(ASTAllVariablesUsedInConvolutionVisitor, self).__init__()
                self.node = node
                self.parent_node = parent_node

            def visit_function_call(self, node):
                func_name = node.get_name()
                if func_name == 'convolve':
                    symbol_buffer = node.get_scope().resolve_to_symbol(str(node.get_args()[1]),
                                                                       SymbolKind.VARIABLE)
                    input_port = get_input_port_by_name(self.parent_node.get_input_blocks(), symbol_buffer.name)
                    if input_port:
                        found_parent_assignment = False
                        node_ = node
                        while not found_parent_assignment:
                            node_ = self.parent_node.get_parent(node_)
                            if isinstance(node_, ASTInlineExpression):   # XXX TODO also needs to accept normal ASTExpression, ASTAssignment?
                                found_parent_assignment = True
                        var_name = node_.get_variable_name()
                        self._variables.append(var_name)

        visitor = ASTAllVariablesUsedInConvolutionVisitor(node, parent_node)
        node.accept(visitor)
        return visitor._variables

    def get_all_variables_assigned_to(self, node):
        class ASTAssignedToVariablesFinderVisitor(ASTVisitor):
            _variables = []

            def __init__(self, synapse):
                super(ASTAssignedToVariablesFinderVisitor, self).__init__()
                self.synapse = synapse


            def visit_assignment(self, node):
                symbol = node.get_scope().resolve_to_symbol(node.get_variable().get_complete_name(), SymbolKind.VARIABLE)
                assert symbol is not None  # should have been checked in a CoCo before
                self._variables.append(symbol)

        if node is None:
            return []

        visitor = ASTAssignedToVariablesFinderVisitor(node)
        node.accept(visitor)

        return [v.name for v in visitor._variables]

    def get_convolve_with_not_post_vars(self, node, neuron_name, synapse_name, parent_node):
        class ASTVariablesUsedInConvolutionVisitor(ASTVisitor):
            _variables = []

            def __init__(self, node: ASTNode, parent_node: ASTNode, codegen_class):
                super(ASTVariablesUsedInConvolutionVisitor, self).__init__()
                self.node = node
                self.parent_node = parent_node
                self.codegen_class = codegen_class

            def visit_function_call(self, node):
                func_name = node.get_name()
                if func_name == 'convolve':
                    symbol_buffer = node.get_scope().resolve_to_symbol(str(node.get_args()[1]),
                                                                       SymbolKind.VARIABLE)
                    input_port = get_input_port_by_name(self.parent_node.get_input_blocks(), symbol_buffer.name)
                    if input_port and not self.codegen_class.is_post_port(input_port.name, neuron_name, synapse_name):
                        found_parent_assignment = False
                        node_ = node
                        while not found_parent_assignment:
                            node_ = self.parent_node.get_parent(node_)
                            if isinstance(node_, ASTInlineExpression):   # XXX TODO also needs to accept normal ASTExpression, ASTAssignment?
                                found_parent_assignment = True
                        var_name = node_.get_variable_name()
                        self._variables.append(var_name)

        if node is None:
            return []

        visitor = ASTVariablesUsedInConvolutionVisitor(node, parent_node, self)
        node.accept(visitor)
        return visitor._variables

    def analyse_transform_neuron_synapse_pairs(self, neurons, synapses):
        """
        Does not modify existing neurons or synapses, but returns lists with additional elements representing new pair neuron and synapse
        """
        if not "neuron_synapse_pairs" in self._options:
            return neurons, synapses

        for neuron_synapse_pair in self._options["neuron_synapse_pairs"]:
            neuron_name = neuron_synapse_pair["neuron"]
            neuron_names = [neuron.get_name() for neuron in neurons]
            if not neuron_name + FrontendConfiguration.suffix in neuron_names:
                raise Exception("Neuron name used in pair ('" + neuron_name + "') not found")  # XXX: log error
                return neurons, synapses
            neuron = neurons[neuron_names.index(neuron_name + FrontendConfiguration.suffix)]
            new_neuron = neuron.clone()

            synapse_name = neuron_synapse_pair["synapse"]
            synapse_names = [synapse.get_name() for synapse in synapses]
            if not synapse_name + FrontendConfiguration.suffix in synapse_names:
                raise Exception("Synapse name used in pair ('" + synapse_name + "') not found")  # XXX: log error
                return neurons, synapses
            synapse = synapses[synapse_names.index(synapse_name + FrontendConfiguration.suffix)]
            synapses.clear()
            new_synapse = synapse.clone()


            #
            #   determine which variables and dynamics in synapse can be transferred to neuron
            #

            all_variables = self.get_all_variables(synapse.get_state_blocks())
            print("All variables defined in state block: " + ", ".join(all_variables))

            all_conv_vars = self.get_all_Variables_used_in_convolutions(synapse.get_equations_blocks(), synapse)
            print("All variables due to convolutions: " + str(all_conv_vars))

            # if any variable is assigned to in the `preReceive` block, is put in the "strictly synaptic" list
            strictly_synaptic_variables = self.get_all_variables_assigned_to(synapse.get_pre_receive())
            print("Assigned-to variables in preReceive: " + ", ".join(strictly_synaptic_variables))

            convolve_with_not_post_vars = self.get_convolve_with_not_post_vars(synapse.get_equations_blocks(), neuron.name, synapse.name, synapse)
            print("Variables used in convolve with other than 'spike post' port: " + str(convolve_with_not_post_vars))

            syn_to_neuron_state_vars = (set(all_variables) | set(all_conv_vars)) - (set(strictly_synaptic_variables) | set(convolve_with_not_post_vars))
            print("--> State variables that will be moved from synapse to neuron: " + str(syn_to_neuron_state_vars))


            #
            #   recursive dependent variables search
            #

            #
            #   collect all the variable/parameter/kernel/function/etc. names used in defining expressions of `syn_to_neuron_state_vars`
            #

            recursive_vars_used = recursive_dependent_variables_search(syn_to_neuron_state_vars, synapse)
            print("recursive dependent variables search yielded the following new variables:")
            print(recursive_vars_used)


            #
            #   move state variable definitions from synapse to neuron
            #

            var_name_suffix = "__for_" + synapse.get_name()

            def get_variable_declarations_from_block(var_name, block):
                decls = []
                for decl in block.get_declarations():
                    for var in decl.get_variables():
                        if var.get_name() == var_name:
                            decls.append(decl)
                            break
                return decls

            def add_suffix_to_variable_names(decl, suffix: str):
                """add suffix to the left-hand side of a declaration"""
                if isinstance(decl, ASTInlineExpression):
                    decl.set_variable_name(decl.get_variable_name() + suffix)
                elif isinstance(decl, ASTOdeEquation):
                    decl.get_lhs().set_name(decl.get_lhs().get_name() + suffix)
                elif isinstance(decl, ASTStmt):
                    # XXX: assume stmt is small_stmt and assignment
                    decl.small_stmt.get_assignment().lhs.set_name(decl.small_stmt.get_assignment().lhs.get_name() + suffix)
                else:
                    for var in decl.get_variables():
                        var.set_name(var.get_name() + suffix)
                # XXX: todo: change variable names inside rhs expressions
                # XXX: todo: change parameter names inside rhs expressions

            vars_used = []

            def move_decl_syn_neuron(state_var, neuron_block, synapse_block, var_name_suffix):
                if not neuron_block \
                   or not synapse_block:
                    return

                decls = get_variable_declarations_from_block(state_var, synapse_block)
                if decls:
                    print("Moving state var definition of " + state_var + " from synapse to neuron")
                    for decl in decls:
                        if decl.has_expression():
                            vars_used.extend(collect_variable_names_in_expression(decl.get_expression()))
                        synapse_block.declarations.remove(decl)
                        add_suffix_to_variable_names(decl, suffix=var_name_suffix)
                        neuron_block.get_declarations().append(decl)
                        decl.update_scope(neuron_block.get_scope())
                        decl.accept(ASTSymbolTableVisitor())

            for state_var in syn_to_neuron_state_vars:
                neuron_block = neuron.get_state_blocks()
                synapse_block = synapse.get_state_blocks()
                move_decl_syn_neuron(state_var, neuron_block, synapse_block, var_name_suffix)


            #
            #   move defining equations for variables from synapse to neuron
            #


            def equations_from_syn_to_neuron(state_var, synapse_block, neuron_block, var_name_suffix, mode):
                if not neuron_block or not synapse_block:
                    return

                assert mode in ["move", "copy"]

                decls = get_eq_declarations_from_block(state_var, synapse_block)

                if decls:
                    #print("\tvar = " + str(state_var) + " from synapse block to neuron")
                    for decl in decls:
                        if (type(decl) in [ASTDeclaration, ASTReturnStmt] and decl.has_expression()) \
                         or type(decl) is ASTInlineExpression:
                            vars_used.extend(collect_variable_names_in_expression(decl.get_expression()))
                        elif type(decl) is ASTOdeEquation:
                            vars_used.extend(collect_variable_names_in_expression(decl.get_rhs()))
                        elif type(decl) is ASTKernel:
                            for expr in decl.get_expressions():
                                vars_used.extend(collect_variable_names_in_expression(expr))
                        else:
                            raise Exception("Tried to move unknown type " + str(type(decl)))

                        if mode == "move":
                            synapse_block.declarations.remove(decl)
                        add_suffix_to_variable_names(decl, suffix=var_name_suffix)
                        neuron_block.get_declarations().append(decl)
                        decl.update_scope(neuron_block.get_scope())
                        decl.accept(ASTSymbolTableVisitor())
                        decl._is_moved_from_syn_to_neuron = True


            for state_var in syn_to_neuron_state_vars:
                print("Moving state var defining equation(s) " + str(state_var))
                equations_from_syn_to_neuron(state_var, new_synapse.get_equations_block(), new_neuron.get_equations_block(), var_name_suffix, mode="move")

            new_neuron._transferred_variables = [neuron_state_var + var_name_suffix for neuron_state_var in syn_to_neuron_state_vars]


            #
            #     mark postsynaptically connected input ports in the synapse
            #     convolutions with them ultimately yield variable updates when post neuron calls emit_spike()
            #

            def mark_post_ports(neuron, synapse):

                post_ports = []

                def mark_post_port(_expr=None):
                    var = None
                    if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                        var = _expr.get_variable()
                    elif isinstance(_expr, ASTVariable):
                        var = _expr

                    if var \
                     and self.is_post_port(var.name, neuron.name, synapse.name):
#                      and get_post_port_by_name(synapse.get_input_blocks(), var.name):
                        print("\tsynapse " + synapse.name + " uses spike port " +str(var.name) + " for neuron " + neuron.name)
                        post_ports.append(var)

                        var._is_post_port = True

                synapse.accept(ASTHigherOrderVisitor(lambda x: mark_post_port(x)))
                return post_ports

            mark_post_ports(new_neuron, new_synapse)


            #
            #     mark variables in the neuron pertaining to synapse postsynaptic ports
            #
            #     convolutions with them ultimately yield variable updates when post neuron calls emit_spike()
            #

            def mark_post_ports(neuron, synapse):

                post_ports = []

                def mark_post_port(_expr=None):
                    var = None
                    if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                        var = _expr.get_variable()
                    elif isinstance(_expr, ASTVariable):
                        var = _expr

                    if var \
                     and self.is_post_port(var.name, neuron.name, synapse.name):
#                      and get_post_port_by_name(synapse.get_input_blocks(), var.name):
                        print("\tneuron " + neuron.name + " uses post spike port " +str(var.name) + " for synapse " + synapse.name)
                        post_ports.append(var)

                        var._is_post_port = True

                neuron.accept(ASTHigherOrderVisitor(lambda x: mark_post_port(x)))
                return post_ports

            mark_post_ports(new_neuron, new_synapse)


            #
            #    move initial values for equations
            #

            def iv_from_syn_to_neuron(state_var, synapse_block, neuron_block, var_name_suffix, mode):
                if not neuron_block \
                 or not synapse_block:
                    return

                assert mode in ["move", "copy"]

                decls = get_eq_declarations_from_block(state_var, synapse_block)

                for decl in decls:
                    if mode == "move":
                        synapse_block.declarations.remove(decl)
                    add_suffix_to_variable_names(decl, suffix=var_name_suffix)
                    neuron_block.get_declarations().append(decl)
                    decl.update_scope(neuron_block.get_scope())
                    decl.accept(ASTSymbolTableVisitor())


            for state_var in syn_to_neuron_state_vars:
                print("Moving state variables for equation(s) " + str(state_var))
                iv_from_syn_to_neuron(state_var, new_synapse.get_state_blocks(), new_neuron.get_state_blocks(), var_name_suffix, mode="move")

            #
            #    move updates in update block from synapse to neuron
            #

            def get_statements_from_block(var_name, block):
                """XXX: only simple statements such as assignments are supported for now. if..then..else compound statements and so are not yet supported."""
                block = block.get_block()
                all_stmts = block.get_stmts()
                stmts = []
                for node in all_stmts:
                    if node.is_small_stmt() \
                     and node.small_stmt.is_assignment() \
                     and node.small_stmt.get_assignment().lhs.get_name() == var_name:
                        stmts.append(node)
                return stmts

            def move_updates_syn_neuron(state_var, synapse_block, neuron_block, var_name_suffix):
                if not neuron_block \
                 or not synapse_block:
                    return

                stmts = get_statements_from_block(state_var, synapse_block)
                if stmts:
                    print("Moving state var updates for " + state_var + " from synapse to neuron")
                    for stmt in stmts:
                        vars_used.extend(collect_variable_names_in_expression(stmt))
                        synapse_block.get_block().stmts.remove(stmt)
                        add_suffix_to_variable_names(stmt, suffix=var_name_suffix)
                        neuron_block.get_block().stmts.append(stmt)
                        stmt.update_scope(neuron_block.get_scope())
                        stmt.accept(ASTSymbolTableVisitor())

            new_neuron.moved_spike_updates = []
            
            for state_var in syn_to_neuron_state_vars:
                print("Moving onPost updates for " + str(state_var))
                """move_updates_syn_neuron(state_var, new_synapse.get_post_receive(), new_neuron.get_update_blocks(), var_name_suffix)"""

                if new_synapse.get_post_receive():
                    stmts = get_statements_from_block(state_var, new_synapse.get_post_receive())
                    if stmts:
                        print("Moving state var updates for " + state_var + " from synapse to neuron")
                        for stmt in stmts:
                            vars_used.extend(collect_variable_names_in_expression(stmt))
                            new_synapse.get_post_receive().get_block().stmts.remove(stmt)
                            add_suffix_to_variable_names(stmt, suffix=var_name_suffix)
#                            neuron_block.get_block().stmts.append(stmt)
                            stmt.update_scope(new_neuron.get_update_blocks().get_scope())
                            stmt.accept(ASTSymbolTableVisitor())
                            new_neuron.moved_spike_updates.append(stmt)


            #
            #    move variable definitions from synapse to neuron
            #

            vars_used = list(set(vars_used))    # pick unique elements
            #print("Dependent variables: " + ", ".join([str(v) for v in vars_used]))
            vars_used = [s for s in vars_used if not var_name_suffix in s.get_name()]
            print("Dependent variables: " + ", ".join([str(v) for v in vars_used]))

            print("Copying declarations from neuron equations block to synapse equations block...")
            for state_var in vars_used:
                print("\t• Copying variable " + str(state_var))
                equations_from_syn_to_neuron(state_var, new_synapse.get_equations_block(), new_neuron.get_equations_block(), var_name_suffix, mode="move")


            #
            #    replace occurrences of the variables in expressions in the original synapse with calls to the corresponding neuron getters
            #

            def replace_variable_name_in_expressions(var_name, synapse, suffix):
                """
                Replace all occurrences of variables (`ASTVariable`s) (e.g. `post_trace'`) with `ASTExternalVariable`s, indicating that they are moved to the postsynaptic partner.
                """
                def replace_var(_expr=None):
                    if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                        var = _expr.get_variable()
                    elif isinstance(_expr, ASTVariable):
                        var = _expr
                    else:
                        return

                    if var.get_name() != var_name:
                        return

                    ast_ext_var = ASTExternalVariable(
                     var.get_name() + suffix,
                     differential_order=var.get_differential_order(),
                     source_position=var.get_source_position())
                    ast_ext_var.update_scope2(new_neuron.get_equations_blocks().get_scope())  # variable reference is in synapse model, referent is variable in neuron model
                    ast_ext_var.accept(ASTSymbolTableVisitor())

                    if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                        print("!! replacement made (var = " + str(ast_ext_var.get_name()) + ") in expression: " + str(synapse.get_parent(_expr)))
                        _expr.set_variable(ast_ext_var)
                    elif isinstance(_expr, ASTVariable):
                        if isinstance(synapse.get_parent(_expr), ASTAssignment):
                            synapse.get_parent(_expr).lhs = ast_ext_var
                            print("!!! replacement made in expression: " + str(synapse.get_parent(_expr)))
                        else:
                            print("Error: unhandled use of variable " + var_name + " in expression " + str(_expr))
                    else:
                        p = synapse.get_parent(var)
                        print("Error: unhandled use of variable " + var_name + " in expression " + str(p))

                synapse.accept(ASTHigherOrderVisitor(lambda x: replace_var(x)))

            print("In synapse: replacing variables with suffixed external variable references")
            for state_var in syn_to_neuron_state_vars:
                print("\t• Replacing variable " + str(state_var))
                replace_variable_name_in_expressions(state_var, new_synapse, var_name_suffix)


            def replace_variable_name_in_expressions1(var_name, astnode, suffix):
                """add suffix to variable names
                """
                def replace_var(_expr=None):
                    if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                        var = _expr.get_variable()
                    elif isinstance(_expr, ASTVariable):
                        var = _expr
                    else:
                        return

                    if not suffix in var.get_name() \
                     and not var.get_name() == "t" \
                     and var.get_name() == var_name:
                        var.set_name(var.get_name() + suffix)

                astnode.accept(ASTHigherOrderVisitor(lambda x: replace_var(x)))

            print("Add suffix to moved variable names in neuron...")
            for state_var in vars_used:
                print("\t• Replacing variable " + str(state_var))
                replace_variable_name_in_expressions1(str(state_var), new_neuron, var_name_suffix)


            #
            #    move parameters
            #

            def param_from_syn_to_neuron(state_var, synapse_block, neuron_block, var_name_suffix, mode):
                if not neuron_block \
                 or not synapse_block:
                    return

                assert mode in ["move", "copy"]

                decls = get_eq_declarations_from_block(state_var, synapse_block)

                for decl in decls:
                    if mode == "move":
                        synapse_block.declarations.remove(decl)
                    add_suffix_to_variable_names(decl, suffix=var_name_suffix)
                    neuron_block.get_declarations().append(decl)
                    decl.update_scope(neuron_block.get_scope())
                    decl.accept(ASTSymbolTableVisitor())

            print("Moving parameters...")
            for state_var in recursive_vars_used:
                print("Moving parameter with name " + str(state_var) + " from synapse to neuron")
                param_from_syn_to_neuron(state_var, new_synapse.get_parameter_blocks(), new_neuron.get_parameter_blocks(), var_name_suffix, mode="move")

            new_neuron.recursive_vars_used = recursive_vars_used


            #
            #     rename neuron
            #

            name_separator_str = "__with_"

            new_neuron_name = neuron.get_name() + name_separator_str + synapse.get_name()
            #self.analytic_solver[new_neuron_name] = self.analytic_solver[neuron.get_name()]
            #self.numeric_solver[new_neuron_name] = self.numeric_solver[neuron.get_name()]
            new_neuron.set_name(new_neuron_name)
            new_neuron.paired_synapse = new_synapse

            #
            #    rename synapse
            #

            new_synapse_name = synapse.get_name() + name_separator_str + neuron.get_name()
            #self.analytic_solver[new_synapse_name] = self.analytic_solver[synapse.get_name()]
            #self.numeric_solver[new_synapse_name] = self.numeric_solver[synapse.get_name()]
            new_synapse.set_name(new_synapse_name)
            new_synapse.paired_neuron = new_neuron
            new_neuron.paired_synapse = new_synapse

            #
            #    add modified versions of neuron and synapse to list
            #

            new_neuron.accept(ASTSymbolTableVisitor())
            new_synapse.accept(ASTSymbolTableVisitor())

            neurons.append(new_neuron)
            synapses.append(new_synapse)

            print("Successfully constructed neuron-synapse pair models")

        return neurons, synapses


    def generate_code(self, neurons, synapses):
        if self._options and "neuron_synapse_pairs" in self._options:
            neurons, synapses = self.analyse_transform_neuron_synapse_pairs(neurons, synapses)
        self.analyse_transform_neurons(neurons)
        self.analyse_transform_synapses(synapses)
        self.generate_neurons(neurons)
        self.generate_synapses(synapses)
        self.generate_module_code(neurons, synapses)


    def generate_module_code(self, neurons: Sequence[ASTNeuron], synapses: Sequence[ASTSynapse]):
        """
        Generates code that is necessary to integrate neuron models into the NEST infrastructure.
        :param neurons: a list of neurons
        :type neurons: list(ASTNeuron)
        """
        namespace = {'neurons': neurons,
                     'synapses': synapses,
                     'moduleName': FrontendConfiguration.get_module_name(),
                     'now': datetime.datetime.utcnow()}
        if not os.path.exists(FrontendConfiguration.get_target_path()):
            os.makedirs(FrontendConfiguration.get_target_path())

        with open(str(os.path.join(FrontendConfiguration.get_target_path(),
                                   FrontendConfiguration.get_module_name())) + '.h', 'w+') as f:
            f.write(str(self._template_module_header.render(namespace)))

        with open(str(os.path.join(FrontendConfiguration.get_target_path(),
                                   FrontendConfiguration.get_module_name())) + '.cpp', 'w+') as f:
            f.write(str(self._template_module_class.render(namespace)))

        with open(str(os.path.join(FrontendConfiguration.get_target_path(),
                                   'CMakeLists')) + '.txt', 'w+') as f:
            f.write(str(self._template_cmakelists.render(namespace)))

        if not os.path.isdir(os.path.realpath(os.path.join(FrontendConfiguration.get_target_path(), 'sli'))):
            os.makedirs(os.path.realpath(os.path.join(FrontendConfiguration.get_target_path(), 'sli')))

        with open(str(os.path.join(FrontendConfiguration.get_target_path(), 'sli',
                                   FrontendConfiguration.get_module_name() + "-init")) + '.sli', 'w+') as f:
            f.write(str(self._template_sli_init.render(namespace)))

        code, message = Messages.get_module_generated(FrontendConfiguration.get_target_path())
        Logger.log_message(None, code, message, None, LoggingLevel.INFO)

    def analyse_transform_neurons(self, neurons: List[ASTNeuron]) -> None:
        """
        Analyse and transform a list of neurons.
        :param neurons: a list of neurons.
        """
        for neuron in neurons:
            code, message = Messages.get_analysing_transforming_neuron(neuron.get_name())
            Logger.log_message(None, code, message, None, LoggingLevel.INFO)
            spike_updates, post_spike_updates = self.analyse_neuron(neuron)
            neuron.spike_updates = spike_updates
            neuron.post_spike_updates = post_spike_updates
            # now store the transformed model
            self.store_transformed_model(neuron)

    def analyse_transform_synapses(self, synapses):
        # type: (list(ASTsynapse)) -> None
        """
        Analyse and transform a list of synapses.
        :param synapses: a list of synapses.
        """
        for synapse in synapses:
            if Logger.logging_level == LoggingLevel.INFO:
                print("Analysing/transforming synapse {}.".format(synapse.get_name()))
            spike_updates, post_spike_updates = self.analyse_synapse(synapse)
            synapse.spike_updates = spike_updates
            # now store the transformed model
            self.store_transformed_model(synapse)

    def get_delta_factors_(self, neuron, equations_block):
        r"""
        For every occurrence of a convolution of the form `x^(n) = a * convolve(kernel, inport) + ...` where `kernel` is a delta function, add the element `(x^(n), inport) --> a` to the set.
        """
        delta_factors = {}
        for ode_eq in equations_block.get_ode_equations():
            var = ode_eq.get_lhs()
            expr = ode_eq.get_rhs()
            conv_calls = OdeTransformer.get_convolve_function_calls(expr)
            for conv_call in conv_calls:
                assert len(
                    conv_call.args) == 2, "convolve() function call should have precisely two arguments: kernel and spike input port"
                kernel = conv_call.args[0]
                if is_delta_kernel(neuron.get_kernel_by_name(kernel.get_variable().get_name())):
                    inport = conv_call.args[1].get_variable()
                    expr_str = str(expr)
                    sympy_expr = sympy.parsing.sympy_parser.parse_expr(expr_str)
                    sympy_expr = sympy.expand(sympy_expr)
                    sympy_conv_expr = sympy.parsing.sympy_parser.parse_expr(str(conv_call))
                    factor_str = []
                    for term in sympy.Add.make_args(sympy_expr):
                        if term.find(sympy_conv_expr):
                            factor_str.append(str(term.replace(sympy_conv_expr, 1)))
                    factor_str = " + ".join(factor_str)
                    delta_factors[(var, inport)] = factor_str

        return delta_factors

    def generate_kernel_buffers_(self, neuron, equations_block):
        """
        For every occurrence of a convolution of the form `convolve(var, spike_buf)`: add the element `(kernel, spike_buf)` to the set, with `kernel` being the kernel that contains variable `var`.
        """

        kernel_buffers = set()
        convolve_calls = OdeTransformer.get_convolve_function_calls(equations_block)
        for convolve in convolve_calls:
            el = (convolve.get_args()[0], convolve.get_args()[1])
            sym = convolve.get_args()[0].get_scope().resolve_to_symbol(
                convolve.get_args()[0].get_variable().name, SymbolKind.VARIABLE)
            if sym is None:
                raise Exception("No initial value(s) defined for kernel with variable \""
                                + convolve.get_args()[0].get_variable().get_complete_name() + "\"")
            if sym.block_type == BlockType.INPUT:
                # swap the order
                el = (el[1], el[0])

            # find the corresponding kernel object
            var = el[0].get_variable()
            assert var is not None
            kernel = neuron.get_kernel_by_name(var.get_name())
            assert kernel is not None, "In convolution \"convolve(" + str(var.name) + ", " + str(
                el[1]) + ")\": no kernel by name \"" + var.get_name() + "\" found in neuron."

            el = (kernel, el[1])
            kernel_buffers.add(el)

        return kernel_buffers

    def replace_convolution_aliasing_inlines(self, neuron):
        """
        Replace all occurrences of kernel names (e.g. ``I_dend`` and ``I_dend'`` for a definition involving a second-order kernel ``inline kernel I_dend = convolve(kern_name, spike_buf)``) with the ODE-toolbox generated variable ``kern_name__X__spike_buf``.
        """
        def replace_var(_expr, replace_var_name: str, replace_with_var_name: str):
            if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                var = _expr.get_variable()
                if var.get_name() == replace_var_name:
                    ast_variable = ASTVariable(replace_with_var_name + '__d' * var.get_differential_order(), differential_order=0)
                    ast_variable.set_source_position(var.get_source_position())
                    _expr.set_variable(ast_variable)

            elif isinstance(_expr, ASTVariable):
                var = _expr
                if var.get_name() == replace_var_name:
                    var.set_name(replace_with_var_name + '__d' * var.get_differential_order())
                    var.set_differential_order(0)

        for decl in neuron.get_equations_block().get_declarations():
            from pynestml.utils.ast_utils import ASTUtils
            if isinstance(decl, ASTInlineExpression) \
               and isinstance(decl.get_expression(), ASTSimpleExpression) \
               and '__X__' in str(decl.get_expression()):
                replace_with_var_name = decl.get_expression().get_variable().get_name()
                neuron.accept(ASTHigherOrderVisitor(lambda x: replace_var(x, decl.get_variable_name(), replace_with_var_name)))


    def replace_variable_names_in_expressions(self, neuron, solver_dicts):
        """
        Replace all occurrences of variables names in NESTML format (e.g. `g_ex$''`)` with the ode-toolbox formatted
        variable name (e.g. `g_ex__DOLLAR__d__d`).

        Variables aliasing convolutions should already have been covered by replace_convolution_aliasing_inlines().
        """
        def replace_var(_expr=None):
            if isinstance(_expr, ASTSimpleExpression) and _expr.is_variable():
                var = _expr.get_variable()
                if variable_in_solver(to_ode_toolbox_processed_name(var.get_complete_name()), solver_dicts):
                    ast_variable = ASTVariable(to_ode_toolbox_processed_name(
                        var.get_complete_name()), differential_order=0)
                    ast_variable.set_source_position(var.get_source_position())
                    _expr.set_variable(ast_variable)

            elif isinstance(_expr, ASTVariable):
                var = _expr
                if variable_in_solver(to_ode_toolbox_processed_name(var.get_complete_name()), solver_dicts):
                    var.set_name(to_ode_toolbox_processed_name(var.get_complete_name()))
                    var.set_differential_order(0)

        def func(x):
            return replace_var(x)

        neuron.accept(ASTHigherOrderVisitor(func))

    def replace_convolve_calls_with_buffers_(self, neuron, equations_block, kernel_buffers):
        r"""
        Replace all occurrences of `convolve(kernel[']^n, spike_input_port)` with the corresponding buffer variable, e.g. `g_E__X__spikes_exc[__d]^n` for a kernel named `g_E` and a spike input port named `spikes_exc`.
        """

        def replace_function_call_through_var(_expr=None):
            if _expr.is_function_call() and _expr.get_function_call().get_name() == "convolve":
                convolve = _expr.get_function_call()
                el = (convolve.get_args()[0], convolve.get_args()[1])
                sym = convolve.get_args()[0].get_scope().resolve_to_symbol(
                    convolve.get_args()[0].get_variable().name, SymbolKind.VARIABLE)
                if sym.block_type == BlockType.INPUT:
                    # swap elements
                    el = (el[1], el[0])
                var = el[0].get_variable()
                spike_input_port = el[1].get_variable()
                kernel = neuron.get_kernel_by_name(var.get_name())

                _expr.set_function_call(None)
                buffer_var = construct_kernel_X_spike_buf_name(
                    var.get_name(), spike_input_port, var.get_differential_order() - 1)
                if is_delta_kernel(kernel):
                    # delta kernels are treated separately, and should be kept out of the dynamics (computing derivates etc.) --> set to zero
                    _expr.set_variable(None)
                    _expr.set_numeric_literal(0)
                else:
                    ast_variable = ASTVariable(buffer_var)
                    ast_variable.set_source_position(_expr.get_source_position())
                    _expr.set_variable(ast_variable)

        def func(x):
            return replace_function_call_through_var(x) if isinstance(x, ASTSimpleExpression) else True

        equations_block.accept(ASTHigherOrderVisitor(func))

    def add_timestep_symbol(self, neuron):
        assert neuron.get_initial_value(
            "__h") is None, "\"__h\" is a reserved name, please do not use variables by this name in your NESTML file"
        assert not "__h" in [sym.name for sym in neuron.get_internal_symbols(
        )], "\"__h\" is a reserved name, please do not use variables by this name in your NESTML file"
        neuron.add_to_internal_block(ModelParser.parse_declaration('__h ms = resolution()'), index=0)

    def analyse_neuron(self, neuron: ASTNeuron) -> List[ASTAssignment]:
        """
        Analyse and transform a single neuron.
        :param neuron: a single neuron.
        :return: spike_updates: list of spike updates, see documentation for get_spike_update_expressions() for more information.
        """
        code, message = Messages.get_start_processing_model(neuron.get_name())
        Logger.log_message(neuron, code, message, neuron.get_source_position(), LoggingLevel.INFO)

        equations_block = neuron.get_equations_block()

        if equations_block is None:
            # add all declared state variables as none of them are used in equations block
            self.non_equations_state_variables[neuron.get_name()] = []
            self.non_equations_state_variables[neuron.get_name()].extend(ASTUtils.all_variables_defined_in_block(neuron.get_state_blocks()))

            return [], []

        delta_factors = self.get_delta_factors_(neuron, equations_block)
        kernel_buffers = self.generate_kernel_buffers_(neuron, equations_block)
        self.replace_convolve_calls_with_buffers_(neuron, equations_block, kernel_buffers)
        self.make_inline_expressions_self_contained(equations_block.get_inline_expressions())
        self.replace_inline_expressions_through_defining_expressions(
            equations_block.get_ode_equations(), equations_block.get_inline_expressions())

        analytic_solver, numeric_solver = self.ode_toolbox_analysis(neuron, kernel_buffers)
        self.analytic_solver[neuron.get_name()] = analytic_solver
        self.numeric_solver[neuron.get_name()] = numeric_solver

        self.non_equations_state_variables[neuron.get_name()] = []
        for decl in neuron.get_state_blocks().get_declarations():
            for var in decl.get_variables():
                # check if this variable is not in equations
                if not neuron.get_equations_blocks():
                    self.non_equations_state_variables[neuron.get_name()].append(var)
                    continue

                used_in_eq = False
                for ode_eq in neuron.get_equations_blocks().get_ode_equations():
                    if ode_eq.get_lhs().get_name() == var.get_name():
                        used_in_eq = True
                        break
                for kern in neuron.get_equations_blocks().get_kernels():
                    for kern_var in kern.get_variables():
                        if kern_var.get_name() == var.get_name():
                            used_in_eq = True
                            break

                if not used_in_eq:
                    self.non_equations_state_variables[neuron.get_name()].append(var)

        self.remove_initial_values_for_kernels(neuron)
        kernels = self.remove_kernel_definitions_from_equations_block(neuron)
        self.update_initial_values_for_odes(neuron, [analytic_solver, numeric_solver])
        self.remove_ode_definitions_from_equations_block(neuron)
        self.create_initial_values_for_kernels(neuron, [analytic_solver, numeric_solver], kernels)
        self.replace_variable_names_in_expressions(neuron, [analytic_solver, numeric_solver])
        self.replace_convolution_aliasing_inlines(neuron)
        self.add_timestep_symbol(neuron)

        if self.analytic_solver[neuron.get_name()] is not None:
            neuron = add_declarations_to_internals(neuron, self.analytic_solver[neuron.get_name()]["propagators"])

        self.update_symbol_table(neuron, kernel_buffers)
        spike_updates, post_spike_updates = self.get_spike_update_expressions(
            neuron, kernel_buffers, [analytic_solver, numeric_solver], delta_factors)

        return spike_updates, post_spike_updates

    '''def analyse_synapse(self, synapse):
        # type: (ASTsynapse) -> None
        """
        Analyse and transform a single synapse.
        :param synapse: a single synapse.
        """
        code, message = Messages.get_start_processing_model(synapse.get_name())
        Logger.log_message(synapse, code, message, synapse.get_source_position(), LoggingLevel.INFO)

        self.add_timestep_symbol(synapse)

        return []'''

    def analyse_synapse(self, synapse):
        # type: (ASTsynapse) -> None
        """
        Analyse and transform a single synapse.
        :param synapse: a single synapse.
        """
        code, message = Messages.get_start_processing_model(synapse.get_name())
        Logger.log_message(synapse, code, message, synapse.get_source_position(), LoggingLevel.INFO)

        equations_block = synapse.get_equations_block()
        spike_updates, post_spike_updates = [], []
        if equations_block is not None:
            delta_factors = self.get_delta_factors_(synapse, equations_block)
            kernel_buffers = self.generate_kernel_buffers_(synapse, equations_block)
            #print("kernel_buffers = " + str([(str(a), str(b)) for a, b in kernel_buffers]))
            self.replace_convolve_calls_with_buffers_(synapse, equations_block, kernel_buffers)
            
            #print("NEST codegenerator step 0...")
            #self.mark_kernel_variable_symbols(synapse, kernel_buffers)
            
            #print("NEST codegenerator step 3...")
            #self.update_symbol_table(synapse, kernel_buffers)

            #print("NEST codegenerator: replacing functions through defining expressions...")
            self.make_inline_expressions_self_contained(equations_block.get_inline_expressions())
            self.replace_inline_expressions_through_defining_expressions(equations_block.get_ode_equations(), equations_block.get_inline_expressions())
            #self.replace_inline_expressions_through_defining_expressions2([analytic_solver, numeric_solver], equations_block.get_inline_expressions())

            analytic_solver, numeric_solver = self.ode_toolbox_analysis(synapse, kernel_buffers)
            self.analytic_solver[synapse.get_name()] = analytic_solver
            self.numeric_solver[synapse.get_name()] = numeric_solver

            self.remove_initial_values_for_kernels(synapse)
            kernels = self.remove_kernel_definitions_from_equations_block(synapse)
            self.update_initial_values_for_odes(synapse, [analytic_solver, numeric_solver])
            self.remove_ode_definitions_from_equations_block(synapse)
            self.create_initial_values_for_kernels(synapse, [analytic_solver, numeric_solver], kernels)
            self.replace_variable_names_in_expressions(synapse, [analytic_solver, numeric_solver])
            self.add_timestep_symbol(synapse)
            self.update_symbol_table(synapse, kernel_buffers)

            #print("NEST codegenerator: Adding ode-toolbox processed kernels to AST...")
            #self.add_kernel_odes(synapse, [analytic_solver, numeric_solver], kernel_buffers)
            #self.replace_convolve_calls_with_buffers_(synapse, equations_block, kernel_buffers)

            if not self.analytic_solver[synapse.get_name()] is None:
                #print("NEST codegenerator: Adding propagators...")
                synapse = add_declarations_to_internals(synapse, self.analytic_solver[synapse.get_name()]["propagators"])

            self.update_symbol_table(synapse, kernel_buffers)
            #self.remove_kernel_definitions_from_equations_block(synapse, self.analytic_solver["state_variables"])

            #if not self.numeric_solver is None:
            #    functional_kernels_to_odes(synapse, self.numeric_solver)

            # update kernel buffers in case direct functions of time have been replaced by higher-order differential

            #print("NEST codegenerator step 5...")
            self.update_symbol_table(synapse, kernel_buffers)

            #print("NEST codegenerator step 6...")
            spike_updates, post_spike_updates = self.get_spike_update_expressions(synapse, kernel_buffers, [analytic_solver, numeric_solver], delta_factors)
        else:
            self.add_timestep_symbol(synapse)


        return spike_updates, post_spike_updates


    def generate_neuron_code(self, neuron):
        # type: (ASTNeuron) -> None
        """
        For a handed over neuron, this method generates the corresponding header and implementation file.
        :param neuron: a single neuron object.
        """
        if not os.path.isdir(FrontendConfiguration.get_target_path()):
            os.makedirs(FrontendConfiguration.get_target_path())
        self.generate_neuron_h_file(neuron)
        self.generate_neuron_cpp_file(neuron)

    def generate_neuron_cpp_file(self, neuron):
        # type: (ASTNeuron) -> None
        """
        For a handed over neuron, this method generates the corresponding implementation file.
        :param neuron: a single neuron object.
        """
        neuron_cpp_file = self._template_neuron_cpp_file.render(self.setup_neuron_generation_helpers(neuron))
        with open(str(os.path.join(FrontendConfiguration.get_target_path(), neuron.get_name())) + '.cpp', 'w+') as f:
            f.write(str(neuron_cpp_file))

    def generate_synapse_code(self, synapse):
        # type: (ASTsynapse) -> None
        """
        For a handed over synapse, this method generates the corresponding header and implementation file.
        :param synapse: a single synapse object.
        """
        if not os.path.isdir(FrontendConfiguration.get_target_path()):
            os.makedirs(FrontendConfiguration.get_target_path())
        self.generate_synapse_h_file(synapse)
        # self.generate_synapse_cpp_file(synapse)


    def generate_neuron_h_file(self, neuron):
        # type: (ASTNeuron) -> None
        """
        For a handed over neuron, this method generates the corresponding header file.
        :param neuron: a single neuron object.
        """
        neuron_h_file = self._template_neuron_h_file.render(self.setup_neuron_generation_helpers(neuron))
        with open(str(os.path.join(FrontendConfiguration.get_target_path(), neuron.get_name())) + '.h', 'w+') as f:
            f.write(str(neuron_h_file))


    def generate_synapse_h_file(self, synapse):
        # type: (ASTsynapse) -> None
        """
        For a handed over synapse, this method generates the corresponding header file.
        :param synapse: a single synapse object.
        """
        synapse_h_file = self._template_synapse_h_file.render(self.setup_synapse_generation_helpers(synapse))
        with open(str(os.path.join(FrontendConfiguration.get_target_path(), synapse.get_name())) + '.h', 'w+') as f:
            f.write(str(synapse_h_file))

    def setup_synapse_generation_helpers(self, synapse: ASTSynapse) -> Dict:
        """
        Returns a standard namespace with often required functionality.
        :param synapse: a single synapse instance
        :return: a map from name to functionality.
        :rtype: dict
        """
        gsl_converter = GSLReferenceConverter()
        gsl_printer = UnitlessExpressionPrinter(gsl_converter)
        # helper classes and objects
        converter = NESTReferenceConverter(False)
        unitless_pretty_printer = UnitlessExpressionPrinter(converter)

        namespace = dict()

        if 'paired_neuron' in dir(synapse):
            namespace['paired_neuron'] = synapse.paired_neuron.get_name()

        namespace['synapseName'] = synapse.get_name()
        namespace['synapse'] = synapse
        namespace['astnode'] = synapse
        namespace['moduleName'] = FrontendConfiguration.get_module_name()
        namespace['printer'] = NestPrinter(unitless_pretty_printer)
        namespace['assignments'] = NestAssignmentsHelper()
        namespace['names'] = NestNamesConverter()
        namespace['declarations'] = NestDeclarationsHelper()
        namespace['utils'] = ASTUtils()
        namespace['idemPrinter'] = UnitlessExpressionPrinter()
        namespace['odeTransformer'] = OdeTransformer()
        namespace['printerGSL'] = gsl_printer
        namespace['now'] = datetime.datetime.utcnow()
        namespace['tracing'] = FrontendConfiguration.is_dev

        namespace['PredefinedUnits'] = pynestml.symbols.predefined_units.PredefinedUnits
        namespace['UnitTypeSymbol'] = pynestml.symbols.unit_type_symbol.UnitTypeSymbol
        namespace['SymbolKind'] = pynestml.symbols.symbol.SymbolKind

        namespace['initial_values'] = {}
        namespace['uses_analytic_solver'] = synapse.get_name() in self.analytic_solver.keys() \
            and self.analytic_solver[synapse.get_name()] is not None
        if namespace['uses_analytic_solver']:
            namespace['analytic_state_variables'] = self.analytic_solver[synapse.get_name()]["state_variables"]
            namespace['analytic_variable_symbols'] = {sym: synapse.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace['analytic_state_variables']}
            namespace['update_expressions'] = {}
            for sym, expr in self.analytic_solver[synapse.get_name()]["initial_values"].items():
                namespace['initial_values'][sym] = expr
            for sym in namespace['analytic_state_variables']:
                expr_str = self.analytic_solver[synapse.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present, as differential equations must have been defined to get here
                expr_ast.update_scope(synapse.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace['update_expressions'][sym] = expr_ast

            namespace['propagators'] = self.analytic_solver[synapse.get_name()]["propagators"]

        namespace['uses_numeric_solver'] = synapse.get_name() in self.analytic_solver.keys() \
            and self.numeric_solver[synapse.get_name()] is not None
        if namespace['uses_numeric_solver']:
            namespace['numeric_state_variables'] = self.numeric_solver[synapse.get_name()]["state_variables"]
            namespace['numeric_variable_symbols'] = {sym: synapse.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace['numeric_state_variables']}
            assert not any([sym is None for sym in namespace['numeric_variable_symbols'].values()])
            namespace['numeric_update_expressions'] = {}
            for sym, expr in self.numeric_solver[synapse.get_name()]["initial_values"].items():
                namespace['initial_values'][sym] = expr
            for sym in namespace['numeric_state_variables']:
                expr_str = self.numeric_solver[synapse.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present, as differential equations must have been defined to get here
                expr_ast.update_scope(synapse.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace['numeric_update_expressions'][sym] = expr_ast

            namespace['useGSL'] = namespace['uses_numeric_solver']
            namespace['names'] = GSLNamesConverter()
            converter = NESTReferenceConverter(True)
            unitless_pretty_printer = UnitlessExpressionPrinter(converter)
            namespace['printer'] = NestPrinter(unitless_pretty_printer)

        namespace["spike_updates"] = synapse.spike_updates

        rng_visitor = ASTRandomNumberGeneratorVisitor()
        synapse.accept(rng_visitor)
        namespace['norm_rng'] = rng_visitor._norm_rng_is_used

        namespace["PyNestMLLexer"] = {}
        from pynestml.generated.PyNestMLLexer import PyNestMLLexer
        for kw in dir(PyNestMLLexer):
            if kw.isupper():
                namespace["PyNestMLLexer"][kw] = eval("PyNestMLLexer." + kw)

        return namespace



    def setup_neuron_generation_helpers(self, neuron: ASTNeuron) -> Dict:
        """
        Returns a standard namespace with often required functionality.
        :param neuron: a single neuron instance
        :type neuron: ASTNeuron
        :return: a map from name to functionality.
        :rtype: dict
        """
        gsl_converter = GSLReferenceConverter()
        gsl_printer = UnitlessExpressionPrinter(gsl_converter)
        # helper classes and objects
        converter = NESTReferenceConverter(False)
        unitless_pretty_printer = UnitlessExpressionPrinter(converter)

        namespace = dict()

        if 'paired_synapse' in dir(neuron):
            namespace['paired_synapse'] = neuron.paired_synapse.get_name()
            namespace["post_spike_updates"] = neuron.post_spike_updates
            if "moved_spike_updates" in dir(neuron):
                namespace["spike_update_stmts"] = neuron.moved_spike_updates
            else:
                namespace["spike_update_stmts"] = []
            namespace['transferred_variables'] = neuron._transferred_variables
            namespace['transferred_variables_syms'] =  {var_name: neuron.scope.resolve_to_symbol(var_name, SymbolKind.VARIABLE) for var_name in namespace['transferred_variables']}
            # {var_name: ASTUtils.get_declaration_by_name(neuron.get_initial_values_blocks(), var_name) for var_name in namespace['transferred_variables']}
        namespace['neuronName'] = neuron.get_name()
        namespace['neuron'] = neuron
        namespace['astnode'] = neuron
        namespace['moduleName'] = FrontendConfiguration.get_module_name()
        namespace['printer'] = NestPrinter(unitless_pretty_printer)
        namespace['assignments'] = NestAssignmentsHelper()
        namespace['names'] = NestNamesConverter()
        namespace['declarations'] = NestDeclarationsHelper()
        namespace['utils'] = ASTUtils()
        namespace['idemPrinter'] = UnitlessExpressionPrinter()
        namespace['outputEvent'] = namespace['printer'].print_output_event(neuron.get_body())
        namespace['has_spike_input'] = ASTUtils.has_spike_input(neuron.get_body())
        namespace['has_continuous_input'] = ASTUtils.has_continuous_input(neuron.get_body())
        namespace['odeTransformer'] = OdeTransformer()
        namespace['printerGSL'] = gsl_printer
        namespace['now'] = datetime.datetime.utcnow()
        namespace['tracing'] = FrontendConfiguration.is_dev

        namespace['neuron_parent_class'] = self.get_option('neuron_parent_class')
        namespace['neuron_parent_class_include'] = self.get_option('neuron_parent_class_include')

        namespace['PredefinedUnits'] = pynestml.symbols.predefined_units.PredefinedUnits
        namespace['UnitTypeSymbol'] = pynestml.symbols.unit_type_symbol.UnitTypeSymbol
        namespace['SymbolKind'] = pynestml.symbols.symbol.SymbolKind

        namespace['initial_values'] = {}
        namespace['uses_analytic_solver'] = neuron.get_name() in self.analytic_solver.keys() \
            and self.analytic_solver[neuron.get_name()] is not None
        if namespace['uses_analytic_solver']:
            namespace['analytic_state_variables_moved'] = []
            if 'paired_synapse' in dir(neuron):
                namespace['analytic_state_variables'] = []
                for sv in self.analytic_solver[neuron.get_name()]["state_variables"]:
                    moved = False
                    for mv in neuron.recursive_vars_used:
                        name_snip = mv + "__"
                        if name_snip == sv[:len(name_snip)]:
                            # this variable was moved from synapse to neuron
                            if not sv in namespace['analytic_state_variables_moved']:
                                namespace['analytic_state_variables_moved'].append(sv)
                                moved = True
                    if not moved:
                        namespace['analytic_state_variables'].append(sv)
                        print("analytic_state_variables: " + str(namespace['analytic_state_variables']))
                namespace['analytic_variable_symbols_moved'] = {sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                    sym, SymbolKind.VARIABLE) for sym in namespace['analytic_state_variables_moved']}
            else:
                namespace['analytic_state_variables'] = self.analytic_solver[neuron.get_name()]["state_variables"]
            namespace['analytic_variable_symbols'] = {sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace['analytic_state_variables']}

            namespace['update_expressions'] = {}
            for sym, expr in self.analytic_solver[neuron.get_name()]["initial_values"].items():
                namespace['initial_values'][sym] = expr
            for sym in namespace['analytic_state_variables'] + namespace['analytic_state_variables_moved']:
                expr_str = self.analytic_solver[neuron.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present, as differential equations must have been defined to get here
                expr_ast.update_scope(neuron.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace['update_expressions'][sym] = expr_ast

            namespace['propagators'] = self.analytic_solver[neuron.get_name()]["propagators"]

        # convert variables from ASTVariable instances to strings
        _names = self.non_equations_state_variables[neuron.get_name()]
        _names = [to_ode_toolbox_processed_name(var.get_complete_name()) for var in _names]
        namespace['non_equations_state_variables'] = _names

        namespace['uses_numeric_solver'] = neuron.get_name() in self.numeric_solver.keys() \
            and self.numeric_solver[neuron.get_name()] is not None
        if namespace['uses_numeric_solver']:

            namespace['numeric_state_variables_moved'] = []
            if 'paired_synapse' in dir(neuron):
                namespace['numeric_state_variables'] = []
                for sv in self.numeric_solver[neuron.get_name()]["state_variables"]:
                    moved = False
                    for mv in neuron.recursive_vars_used:
                        name_snip = mv + "__"
                        if name_snip == sv[:len(name_snip)]:
                            # this variable was moved from synapse to neuron
                            if not sv in namespace['numeric_state_variables_moved']:
                                namespace['numeric_state_variables_moved'].append(sv)
                                moved = True
                    if not moved:
                        namespace['numeric_state_variables'].append(sv)
                        print("numeric_state_variables: " + str(namespace['numeric_state_variables']))
                namespace['numeric_variable_symbols_moved'] = {sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                    sym, SymbolKind.VARIABLE) for sym in namespace['numeric_state_variables_moved']}
            else:
                namespace['numeric_state_variables'] = self.numeric_solver[neuron.get_name()]["state_variables"]

            namespace['numeric_variable_symbols'] = {sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace['numeric_state_variables']}
            assert not any([sym is None for sym in namespace['numeric_variable_symbols'].values()])
            namespace['numeric_update_expressions'] = {}
            for sym, expr in self.numeric_solver[neuron.get_name()]["initial_values"].items():
                namespace['initial_values'][sym] = expr
            for sym in namespace['numeric_state_variables'] + namespace['numeric_state_variables_moved']:
                expr_str = self.numeric_solver[neuron.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present, as differential equations must have been defined to get here
                expr_ast.update_scope(neuron.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace['numeric_update_expressions'][sym] = expr_ast

            if namespace['uses_numeric_solver']:
                if 'analytic_state_variables_moved' in namespace.keys():
                    namespace['purely_numeric_state_variables_moved'] = list(set(namespace['numeric_state_variables_moved']) - set(namespace['analytic_state_variables_moved']))
                else:
                    namespace['purely_numeric_state_variables_moved'] = namespace['numeric_state_variables_moved']

            namespace['useGSL'] = namespace['uses_numeric_solver']
            namespace['names'] = GSLNamesConverter()
            converter = NESTReferenceConverter(True)
            unitless_pretty_printer = UnitlessExpressionPrinter(converter)
            namespace['printer'] = NestPrinter(unitless_pretty_printer)

        namespace["spike_updates"] = neuron.spike_updates

        namespace["recordable_state_variables"] = [sym for sym in neuron.get_state_symbols() if namespace['declarations'].get_domain_from_type(sym.get_type_symbol()) == "double" and sym.is_recordable and not is_delta_kernel(neuron.get_kernel_by_name(sym.name))]
        namespace["recordable_inline_expressions"] = [sym for sym in neuron.get_inline_expression_symbols() if namespace['declarations'].get_domain_from_type(sym.get_type_symbol()) == "double" and sym.is_recordable]

        namespace["parameter_syms_with_iv"] = [sym for sym in neuron.get_parameter_symbols() if sym.has_declaring_expression() and (not neuron.get_kernel_by_name(sym.name))]

        rng_visitor = ASTRandomNumberGeneratorVisitor()
        neuron.accept(rng_visitor)
        namespace['norm_rng'] = rng_visitor._norm_rng_is_used

        return namespace

    def ode_toolbox_analysis(self, neuron: ASTNeuron, kernel_buffers: Mapping[ASTKernel, ASTInputPort]):
        """
        Prepare data for ODE-toolbox input format, invoke ODE-toolbox analysis via its API, and return the output.
        """
        assert isinstance(neuron.get_equations_blocks(), ASTEquationsBlock), "only one equation block should be present"

        equations_block = neuron.get_equations_block()

        if len(equations_block.get_kernels()) == 0 and len(equations_block.get_ode_equations()) == 0:
            # no equations defined -> no changes to the neuron
            return None, None

        code, message = Messages.get_neuron_analyzed(neuron.get_name())
        Logger.log_message(neuron, code, message, neuron.get_source_position(), LoggingLevel.INFO)

        parameters_block = neuron.get_parameter_blocks()
        odetoolbox_indict = self.transform_ode_and_kernels_to_json(neuron, parameters_block, kernel_buffers)
        odetoolbox_indict["options"] = {}
        odetoolbox_indict["options"]["output_timestep_symbol"] = "__h"
        solver_result = analysis(odetoolbox_indict,
                                 disable_stiffness_check=True,
                                 preserve_expressions=self.get_option('preserve_expressions'),
                                 simplify_expression=self.get_option('simplify_expression'),
                                 log_level=FrontendConfiguration.logging_level)
        analytic_solver = None
        analytic_solvers = [x for x in solver_result if x["solver"] == "analytical"]
        assert len(analytic_solvers) <= 1, "More than one analytic solver not presently supported"
        if len(analytic_solvers) > 0:
            analytic_solver = analytic_solvers[0]

        # if numeric solver is required, generate a stepping function that includes each state variable
        numeric_solver = None
        numeric_solvers = [x for x in solver_result if x["solver"].startswith("numeric")]
        if numeric_solvers:
            solver_result = analysis(odetoolbox_indict,
                                     disable_stiffness_check=True,
                                     disable_analytic_solver=True,
                                     preserve_expressions=self.get_option('preserve_expressions'),
                                     simplify_expression=self.get_option('simplify_expression'),
                                     log_level=FrontendConfiguration.logging_level)
            numeric_solvers = [x for x in solver_result if x["solver"].startswith("numeric")]
            assert len(numeric_solvers) <= 1, "More than one numeric solver not presently supported"
            if len(numeric_solvers) > 0:
                numeric_solver = numeric_solvers[0]

        return analytic_solver, numeric_solver

    def update_symbol_table(self, neuron, kernel_buffers):
        """
        Update symbol table and scope.
        """
        SymbolTable.delete_neuron_scope(neuron.get_name())
        symbol_table_visitor = ASTSymbolTableVisitor()
        symbol_table_visitor.after_ast_rewrite_ = True
        neuron.accept(symbol_table_visitor)
        SymbolTable.add_neuron_scope(neuron.get_name(), neuron.get_scope())

    def remove_initial_values_for_kernels(self, neuron):
        """
        Remove initial values for original declarations (e.g. g_in, g_in', V_m); these might conflict with the initial value expressions returned from ODE-toolbox.
        """
        assert isinstance(neuron.get_equations_blocks(), ASTEquationsBlock), "only one equation block should be present"

        equations_block = neuron.get_equations_block()
        symbols_to_remove = set()
        for kernel in equations_block.get_kernels():
            for kernel_var in kernel.get_variables():
                kernel_var_order = kernel_var.get_differential_order()
                for order in range(kernel_var_order):
                    symbol_name = kernel_var.get_name() + "'" * order
                    symbols_to_remove.add(symbol_name)

        decl_to_remove = set()
        for symbol_name in symbols_to_remove:
            for decl in neuron.get_state_blocks().get_declarations():
                if len(decl.get_variables()) == 1:
                    if decl.get_variables()[0].get_name() == symbol_name:
                        decl_to_remove.add(decl)
                else:
                    for var in decl.get_variables():
                        if var.get_name() == symbol_name:
                            decl.variables.remove(var)

        for decl in decl_to_remove:
            neuron.get_state_blocks().get_declarations().remove(decl)

    def update_initial_values_for_odes(self, neuron, solver_dicts) -> None:
        """
        Update initial values for original ODE declarations (e.g. V_m', g_ahp'') that are present in the model
        before ODE-toolbox processing, with the formatted variable names and initial values returned by ODE-toolbox.
        """
        assert isinstance(neuron.get_equations_blocks(), ASTEquationsBlock), "only one equation block should be present"

        if neuron.get_state_blocks() is None:
            return

        for iv_decl in neuron.get_state_blocks().get_declarations():
            for var in iv_decl.get_variables():
                var_name = var.get_complete_name()
                if is_ode_variable(var.get_name(), neuron):
                    assert variable_in_solver(to_ode_toolbox_processed_name(var_name), solver_dicts)

                    # replace the left-hand side variable name by the ode-toolbox format
                    var.set_name(to_ode_toolbox_processed_name(var.get_complete_name()))
                    var.set_differential_order(0)

                    # replace the defining expression by the ode-toolbox result
                    iv_expr = get_initial_value_from_ode_toolbox_result(
                        to_ode_toolbox_processed_name(var_name), solver_dicts)
                    assert iv_expr is not None
                    iv_expr = ModelParser.parse_expression(iv_expr)
                    iv_expr.update_scope(neuron.get_state_blocks().get_scope())
                    iv_decl.set_expression(iv_expr)

    def _get_ast_variable(self, neuron, var_name) -> Optional[ASTVariable]:
        """
        Grab the ASTVariable corresponding to the initial value by this name
        """
        for decl in neuron.get_state_blocks().get_declarations():
            for var in decl.variables:
                if var.get_name() == var_name:
                    return var
        return None

    def create_initial_values_for_kernels(self, neuron, solver_dicts, kernels):
        """
        Add the variables used in kernels from the ode-toolbox result dictionary as ODEs in NESTML AST
        """
        for solver_dict in solver_dicts:
            if solver_dict is None:
                continue
            for var_name in solver_dict["initial_values"].keys():
                if variable_in_kernels(var_name, kernels):
                    # original initial value expressions should have been removed to make place for ode-toolbox results
                    assert not declaration_in_state_block(neuron, var_name)

        for solver_dict in solver_dicts:
            if solver_dict is None:
                continue

            for var_name, expr in solver_dict["initial_values"].items():
                # here, overwrite is allowed because initial values might be repeated between numeric and analytic solver
                if variable_in_kernels(var_name, kernels):
                    expr = "0"    # for kernels, "initial value" returned by ode-toolbox is actually the increment value; the actual initial value is assumed to be 0
                    if not declaration_in_state_block(neuron, var_name):
                        add_declaration_to_state_block(neuron, var_name, expr)

    def create_initial_values_for_ode_toolbox_odes(self, neuron, solver_dicts, kernel_buffers, kernels):
        """
        Add the variables used in ODEs from the ode-toolbox result dictionary as ODEs in NESTML AST.
        """
        for solver_dict in solver_dicts:
            if solver_dict is None:
                continue
            for var_name in solver_dict["initial_values"].keys():
                # original initial value expressions should have been removed to make place for ode-toolbox results
                assert not declaration_in_state_block(neuron, var_name)

        for solver_dict in solver_dicts:
            if solver_dict is None:
                continue

            for var_name, expr in solver_dict["initial_values"].items():
                # here, overwrite is allowed because initial values might be repeated between numeric and analytic solver

                if variable_in_kernels(var_name, kernels):
                    expr = "0"    # for kernels, "initial value" returned by ode-toolbox is actually the increment value; the actual initial value is assumed to be 0

                if not declaration_in_state_block(neuron, var_name):
                    add_declaration_to_state_block(neuron, var_name, expr)


    def get_spike_update_expressions(self, neuron: ASTNeuron, kernel_buffers, solver_dicts, delta_factors) -> List[ASTAssignment]:
        """
        Generate the equations that update the dynamical variables when incoming spikes arrive. To be invoked after ode-toolbox.

        For example, a resulting `assignment_str` could be "I_kernel_in += (in_spikes/nS) * 1". The values are taken from the initial values for each corresponding dynamical variable, either from ode-toolbox or directly from user specification in the model.

        Note that for kernels, `initial_values` actually contains the increment upon spike arrival, rather than the initial value of the corresponding ODE dimension.
        """
        spike_updates = []
        post_spike_updates = {}

        for kernel, spike_input_port in kernel_buffers:
            if is_delta_kernel(kernel):
                continue

            '''if neuron.get_scope().resolve_to_symbol(str(spike_input_port), SymbolKind.VARIABLE) is None:
                print("failure resolving symbol: "+ str(spike_input_port))
                # this case covers variables that were moved from synapse to the neuron
                continue'''

            '''if not ("_is_post_port" in dir(spike_input_port.get_variable()) \
             and spike_input_port.get_variable()._is_post_port):
                # not a post port
                raise Exception("Input port " + str(spike_input_port) + " not found")'''

            if "_is_post_port" in dir(spike_input_port.get_variable()) \
             and spike_input_port.get_variable()._is_post_port:
                # it's a port in the neuron ??? that receives post spikes ???
                orig_port_name = str(spike_input_port)[:str(spike_input_port).index("__for_")]
                buffer_type = neuron.paired_synapse.get_scope().resolve_to_symbol(orig_port_name, SymbolKind.VARIABLE).get_type_symbol()
            else:
                buffer_type = neuron.get_scope().resolve_to_symbol(str(spike_input_port), SymbolKind.VARIABLE).get_type_symbol()

            assert not buffer_type is None

            for kernel_var in kernel.get_variables():
                for var_order in range(get_kernel_var_order_from_ode_toolbox_result(kernel_var.get_name(), solver_dicts)):
                    kernel_spike_buf_name = construct_kernel_X_spike_buf_name(
                        kernel_var.get_name(), spike_input_port, var_order)
                    expr = get_initial_value_from_ode_toolbox_result(kernel_spike_buf_name, solver_dicts)
                    assert expr is not None, "Initial value not found for kernel " + kernel_var
                    expr = str(expr)
                    if expr in ["0", "0.", "0.0"]:
                        continue    # skip adding the statement if we're only adding zero

                    assignment_str = kernel_spike_buf_name + " += "
                    if "_is_post_port" in dir(spike_input_port.get_variable()) \
                     and spike_input_port.get_variable()._is_post_port:
                        assignment_str += "1."
                    else:
                        assignment_str += "(" + str(spike_input_port) + ")"
                    if not expr in ["1.", "1.0", "1"]:
                        assignment_str += " * (" + \
                            self._printer.print_expression(ModelParser.parse_expression(expr)) + ")"

                    if not buffer_type.print_nestml_type() in ["1.", "1.0", "1"]:
                        assignment_str += " / (" + buffer_type.print_nestml_type() + ")"

                    ast_assignment = ModelParser.parse_assignment(assignment_str)
                    ast_assignment.update_scope(neuron.get_scope())
                    ast_assignment.accept(ASTSymbolTableVisitor())

                    if neuron.get_scope().resolve_to_symbol(str(spike_input_port), SymbolKind.VARIABLE) is None:
                        print("failure resolving symbol: "+ str(spike_input_port))
                        # this case covers variables that were moved from synapse to the neuron
                        post_spike_updates[kernel_var.get_name()] = ast_assignment
                    elif "_is_post_port" in dir(spike_input_port.get_variable()) \
                     and spike_input_port.get_variable()._is_post_port:
                        print("adding post assignment string: " + str(ast_assignment))
                        spike_updates.append(ast_assignment)
                    else:
                        spike_updates.append(ast_assignment)

        for k, factor in delta_factors.items():
            var = k[0]
            inport = k[1]
            assignment_str = var.get_name() + "'" * (var.get_differential_order() - 1) + " += "
            if not factor in ["1.", "1.0", "1"]:
                assignment_str += "(" + self._printer.print_expression(ModelParser.parse_expression(factor)) + ") * "
            assignment_str += str(inport)
            ast_assignment = ModelParser.parse_assignment(assignment_str)
            ast_assignment.update_scope(neuron.get_scope())
            ast_assignment.accept(ASTSymbolTableVisitor())

            spike_updates.append(ast_assignment)

        return spike_updates, post_spike_updates

    def remove_kernel_definitions_from_equations_block(self, neuron):
        """
        Removes all kernels in this block.
        """
        equations_block = neuron.get_equations_block()

        decl_to_remove = set()
        for decl in equations_block.get_declarations():
            if type(decl) is ASTKernel:
                decl_to_remove.add(decl)

        for decl in decl_to_remove:
            equations_block.get_declarations().remove(decl)

        return decl_to_remove

    def remove_ode_definitions_from_equations_block(self, neuron):
        """
        Removes all ODEs in this block.
        """
        equations_block = neuron.get_equations_block()

        decl_to_remove = set()
        for decl in equations_block.get_ode_equations():
            decl_to_remove.add(decl)

        for decl in decl_to_remove:
            equations_block.get_declarations().remove(decl)

    def transform_ode_and_kernels_to_json(self, neuron: ASTNeuron, parameters_block, kernel_buffers):
        """
        Converts AST node to a JSON representation suitable for passing to ode-toolbox.

        Each kernel has to be generated for each spike buffer convolve in which it occurs, e.g. if the NESTML model code contains the statements

            convolve(G, ex_spikes)
            convolve(G, in_spikes)

        then `kernel_buffers` will contain the pairs `(G, ex_spikes)` and `(G, in_spikes)`, from which two ODEs will be generated, with dynamical state (variable) names `G__X__ex_spikes` and `G__X__in_spikes`.

        :param equations_block: ASTEquationsBlock
        :return: Dict
        """
        odetoolbox_indict = {}

        gsl_converter = ODEToolboxReferenceConverter()
        gsl_printer = UnitlessExpressionPrinter(gsl_converter)

        odetoolbox_indict["dynamics"] = []
        equations_block = neuron.get_equations_block()
        for equation in equations_block.get_ode_equations():
            # n.b. includes single quotation marks to indicate differential order
            lhs = to_ode_toolbox_name(equation.get_lhs().get_complete_name())
            rhs = gsl_printer.print_expression(equation.get_rhs())
            entry = {"expression": lhs + " = " + rhs}
            symbol_name = equation.get_lhs().get_name()
            symbol = equations_block.get_scope().resolve_to_symbol(symbol_name, SymbolKind.VARIABLE)

            entry["initial_values"] = {}
            symbol_order = equation.get_lhs().get_differential_order()
            for order in range(symbol_order):
                iv_symbol_name = symbol_name + "'" * order
                initial_value_expr = neuron.get_initial_value(iv_symbol_name)
                if initial_value_expr:
                    expr = gsl_printer.print_expression(initial_value_expr)
                    entry["initial_values"][to_ode_toolbox_name(iv_symbol_name)] = expr
            odetoolbox_indict["dynamics"].append(entry)

        # write a copy for each (kernel, spike buffer) combination
        for kernel, spike_input_port in kernel_buffers:

            if is_delta_kernel(kernel):
                # delta function -- skip passing this to ode-toolbox
                continue

            for kernel_var in kernel.get_variables():
                expr = get_expr_from_kernel_var(kernel, kernel_var.get_complete_name())
                kernel_order = kernel_var.get_differential_order()
                kernel_X_spike_buf_name_ticks = construct_kernel_X_spike_buf_name(
                    kernel_var.get_name(), spike_input_port, kernel_order, diff_order_symbol="'")

                replace_rhs_variables(expr, kernel_buffers)

                entry = {}
                entry["expression"] = kernel_X_spike_buf_name_ticks + " = " + str(expr)

                # initial values need to be declared for order 1 up to kernel order (e.g. none for kernel function f(t) = ...; 1 for kernel ODE f'(t) = ...; 2 for f''(t) = ... and so on)
                entry["initial_values"] = {}
                for order in range(kernel_order):
                    iv_sym_name_ode_toolbox = construct_kernel_X_spike_buf_name(
                        kernel_var.get_name(), spike_input_port, order, diff_order_symbol="'")
                    symbol_name_ = kernel_var.get_name() + "'" * order
                    symbol = equations_block.get_scope().resolve_to_symbol(symbol_name_, SymbolKind.VARIABLE)
                    assert symbol is not None, "Could not find initial value for variable " + symbol_name_
                    initial_value_expr = symbol.get_declaring_expression()
                    assert initial_value_expr is not None, "No initial value found for variable name " + symbol_name_
                    entry["initial_values"][iv_sym_name_ode_toolbox] = gsl_printer.print_expression(initial_value_expr)

                odetoolbox_indict["dynamics"].append(entry)

        odetoolbox_indict["parameters"] = {}
        if parameters_block is not None:
            for decl in parameters_block.get_declarations():
                for var in decl.variables:
                    odetoolbox_indict["parameters"][var.get_complete_name(
                    )] = gsl_printer.print_expression(decl.get_expression())

        return odetoolbox_indict

    def make_inline_expressions_self_contained(self, inline_expressions: List[ASTInlineExpression]) -> List[ASTInlineExpression]:
        """
        Make inline_expressions self contained, i.e. without any references to other inline_expressions.

        TODO: it should be a method inside of the ASTInlineExpression
        TODO: this should be done by means of a visitor

        :param inline_expressions: A sorted list with entries ASTInlineExpression.
        :return: A list with ASTInlineExpressions. Defining expressions don't depend on each other.
        """
        for source in inline_expressions:
            source_position = source.get_source_position()
            for target in inline_expressions:
                matcher = re.compile(self._variable_matching_template.format(source.get_variable_name()))
                target_definition = str(target.get_expression())
                target_definition = re.sub(matcher, "(" + str(source.get_expression()) + ")", target_definition)
                target.expression = ModelParser.parse_expression(target_definition)
                target.expression.update_scope(source.get_scope())
                target.expression.accept(ASTSymbolTableVisitor())

                def log_set_source_position(node):
                    if node.get_source_position().is_added_source_position():
                        node.set_source_position(source_position)

                target.expression.accept(ASTHigherOrderVisitor(visit_funcs=log_set_source_position))

        return inline_expressions

    def replace_inline_expressions_through_defining_expressions(self,
                                                                definitions: Sequence[ASTOdeEquation],
                                                                inline_expressions: Sequence[ASTInlineExpression]) -> Sequence[ASTOdeEquation]:
        """
        Replaces symbols from `inline_expressions` in `definitions` with corresponding defining expressions from `inline_expressions`.

        :param definitions: A list of ODE definitions (**updated in-place**).
        :param inline_expressions: A list of inline expression definitions.
        :return: A list of updated ODE definitions (same as the ``definitions`` parameter).
        """
        for m in inline_expressions:
            source_position = m.get_source_position()
            for target in definitions:
                matcher = re.compile(self._variable_matching_template.format(m.get_variable_name()))
                target_definition = str(target.get_rhs())
                target_definition = re.sub(matcher, "(" + str(m.get_expression()) + ")", target_definition)
                target.rhs = ModelParser.parse_expression(target_definition)
                target.update_scope(m.get_scope())
                target.accept(ASTSymbolTableVisitor())

                def log_set_source_position(node):
                    if node.get_source_position().is_added_source_position():
                        node.set_source_position(source_position)

                target.accept(ASTHigherOrderVisitor(visit_funcs=log_set_source_position))

        return definitions

    def store_transformed_model(self, ast):
        if FrontendConfiguration.store_log:
            with open(str(os.path.join(FrontendConfiguration.get_target_path(), '..', 'report',
                                       ast.get_name())) + '.txt', 'w+') as f:
                f.write(str(ast))
