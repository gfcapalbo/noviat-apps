# -*- encoding: utf-8 -*-
##############################################################################
#
#    Odoo, Open Source Management Solution
#
#    Copyright (c) 2009-2015 Noviat nv/sa (www.noviat.com).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

try:
    import cStringIO as StringIO
except ImportError:
    import StringIO
import base64
import csv
from datetime import datetime
from sys import exc_info
from traceback import format_exception

from openerp.osv import orm, fields
from openerp.tools.translate import _
from openerp.exceptions import Warning

import logging
_logger = logging.getLogger(__name__)


class AccountMoveLineImport(orm.TransientModel):
    _name = 'aml.import'
    _description = 'Import account move lines'

    _columns = {
        'aml_data': fields.binary('File', required=True),
        'aml_fname': fields.char('Filename'),
        'csv_separator': fields.selection(
            [(',', ' . (comma)'), (';', ', (semicolon)')],
            'CSV Separator', required=True),
        'decimal_separator': fields.selection(
            [('.', ' . (dot)'), (',', ', (comma)')],
            'Decimal Separator', required=True),
        'codepage': fields.char(
            'Code Page',
            help="Code Page of the system that has generated the csv file."
                 "\nE.g. Windows-1252, utf-8"),
        'note': fields.text('Log'),
    }

    _defaults = {
        'codepage':
            lambda self, cr, uid, context: self._default_codepage(
                cr, uid, context=context),
        'csv_separator':
            lambda self, cr, uid, context: self._default_csv_separator(
                cr, uid, context=context),
        'decimal_separator':
            lambda self, cr, uid, context: self._default_decimal_separator(
                cr, uid, context=context),
    }

    def _default_codepage(self, cr, uid, context=None):
        return 'Windows-1252'

    def _default_csv_separator(self, cr, uid, context=None):
        return ';'

    def _default_decimal_separator(self, cr, uid, context=None):
        return ','

    def _remove_leading_lines(self, lines, context=None):
        """ remove leading blank or comment lines """
        input = StringIO.StringIO(lines)
        header = False
        while not header:
            ln = input.next()
            if not ln or ln and ln[0] in [self._csv_separator, '#']:
                continue
            else:
                header = ln.lower()
        if not header:
            raise Warning(
                _("No header line found in the input file !"))
        output = input.read()
        return output, header

    def _compute_lines(self, cr, uid, aml_data, context=None):
        lines_in = base64.decodestring(aml_data)
        lines, header = self._remove_leading_lines(
            lines_in, context=context)
        try:
            dialect = csv.Sniffer().sniff(
                lines[:128], delimiters=';,')
        except:
            """
            csv.Sniffer is not always reliable
            in the detection of the delimiter
            """
            dialect = csv.Sniffer().sniff(
                '"header 1";"header 2";\r\n')
        dialect.delimiter = str(self._csv_separator)
        return lines, header, dialect

    def _input_fields(self):
        """
        Extend this dictionary if you want to add support for
        fields requiring pre-processing before being added to
        the move line values dict.
        """
        res = {
            'account': {'method': self._handle_account},
            'account_id': {'required': True},
            'debit': {'method': self._handle_debit, 'required': True},
            'credit': {'method': self._handle_credit, 'required': True},
            'partner': {'method': self._handle_partner},
            'product': {'method': self._handle_product},
            'date_maturity': {'method': self._handle_date_maturity},
            'due date': {'method': self._handle_date_maturity},
            'currency': {'method': self._handle_currency},
            'tax account': {'method': self._handle_tax_code},
            'tax_code': {'method': self._handle_tax_code},
            'analytic account': {'method': self._handle_analytic_account},
        }
        return res

    def _get_orm_fields(self, cr, uid, context=None):
        aml_obj = self.pool['account.move.line']
        orm_fields = aml_obj.fields_get(cr, uid, context=context)
        blacklist = orm.MAGIC_COLUMNS + [aml_obj.CONCURRENCY_CHECK_FIELD]
        self._orm_fields = {}
        for f in orm_fields:
            if f not in blacklist and not orm_fields[f].get('depends'):
                self._orm_fields[f] = orm_fields[f]

    def _process_header(self, cr, uid, header_fields, context=None):

        self._field_methods = self._input_fields()
        self._skip_fields = []

        # header fields after blank column are considered as comments
        column_cnt = 0
        for cnt in range(len(header_fields)):
            if header_fields[cnt] == '':
                column_cnt = cnt
                break
            elif cnt == len(header_fields) - 1:
                column_cnt = cnt + 1
                break
        header_fields = header_fields[:column_cnt]

        # check for duplicate header fields
        header_fields2 = []
        for hf in header_fields:
            if hf in header_fields2:
                raise Warning(_(
                    "Duplicate header field '%s' found !"
                    "\nPlease correct the input file.")
                    % hf)
            else:
                header_fields2.append(hf)

        for i, hf in enumerate(header_fields):

            if hf in self._field_methods:
                continue

            if hf not in self._orm_fields \
                    and hf not in [self._orm_fields[f]['string'].lower()
                                   for f in self._orm_fields]:
                _logger.error(
                    _("%s, undefined field '%s' found "
                      "while importing move lines"),
                    self._name, hf)
                self._skip_fields.append(hf)
                continue

            field_def = self._orm_fields.get(hf)
            if not field_def:
                for f in self._orm_fields:
                    if self._orm_fields[f]['string'].lower() == hf:
                        orm_field = f
                        field_def = self._orm_fields.get(f)
                        break
            else:
                orm_field = hf
            field_type = field_def['type']

            if field_type in ['char', 'text']:
                self._field_methods[hf] = {
                    'method': self._handle_orm_char,
                    'orm_field': orm_field,
                    }
            elif field_type == 'integer':
                self._field_methods[hf] = {
                    'method': self._handle_orm_integer,
                    'orm_field': orm_field,
                    }
            elif field_type == 'float':
                self._field_methods[hf] = {
                    'method': self._handle_orm_float,
                    'orm_field': orm_field,
                    }
            elif field_type == 'many2one':
                self._field_methods[hf] = {
                    'method': self._handle_orm_many2one,
                    'orm_field': orm_field,
                    }
            else:
                _logger.error(
                    _("%s, the import of ORM fields of type '%s' "
                      "is not supported"),
                    self._name, hf, field_type)
                self._skip_fields.append(hf)

        return header_fields

    def _log_line_error(self, line, msg):
        data = self._csv_separator.join(
            [line[hf] for hf in self._header_fields])
        self._err_log += _(
            "Error when processing line '%s'") % data + ':\n' + msg + '\n\n'

    def _handle_orm_char(self, cr, uid, field, line, move, aml_vals,
                         orm_field=False, context=None):
        orm_field = orm_field or field
        if not aml_vals.get(orm_field):
            aml_vals[orm_field] = line[field]

    def _handle_orm_integer(self, cr, uid, field, line, move, aml_vals,
                            orm_field=False, context=None):
        orm_field = orm_field or field
        if not aml_vals.get(orm_field):
            val = str2int(
                line[field], self._decimal_separator)
            if val is False:
                msg = _(
                    "Incorrect value '%s' "
                    "for field '%s' of type Integer !"
                    ) % (line[field], field)
                self._log_line_error(line, msg)
            else:
                aml_vals[orm_field] = val

    def _handle_orm_float(self, cr, uid, field, line, move, aml_vals,
                          orm_field=False, context=None):
        orm_field = orm_field or field
        if not aml_vals.get(orm_field):
            aml_vals[orm_field] = str2float(
                line[field], self._decimal_separator)

            val = str2float(
                line[field], self._decimal_separator)
            if val is False:
                msg = _(
                    "Incorrect value '%s' "
                    "for field '%s' of type Numeric !"
                    ) % (line[field], field)
                self._log_line_error(line, msg)
            else:
                aml_vals[orm_field] = val

    def _handle_orm_many2one(self, cr, uid, field, line, move, aml_vals,
                             orm_field=False, context=None):
        orm_field = orm_field or field
        if not aml_vals.get(orm_field):
            val = str2int(
                line[field], self._decimal_separator)
            if val is False:
                msg = _(
                    "Incorrect value '%s' "
                    "for field '%s' of type Many2One !"
                    "\nYou should specify the database key "
                    "or contact your IT department "
                    "to add support for this field."
                    ) % (line[field], field)
                self._log_line_error(line, msg)
            else:
                aml_vals[orm_field] = val

    def _handle_account(self, cr, uid,
                        field, line, move, aml_vals, context=None):
        if not aml_vals.get('account_id'):
            code = line[field]
            if code in self._accounts_dict:
                aml_vals['account_id'] = self._accounts_dict[code]
            else:
                msg = _("Account with code '%s' not found !") % code
                self._log_line_error(line, msg)

    def _handle_debit(self, cr, uid,
                      field, line, move, aml_vals, context=None):
        if 'debit' not in aml_vals:
            debit = str2float(line[field], self._decimal_separator)
            aml_vals['debit'] = debit
            self._sum_debit += debit

    def _handle_credit(self, cr, uid,
                       field, line, move, aml_vals, context=None):
        if 'credit' not in aml_vals:
            credit = str2float(line[field], self._decimal_separator)
            aml_vals['credit'] = credit
            self._sum_credit += credit

    def _handle_partner(self, cr, uid,
                        field, line, move, aml_vals, context=None):
        if not aml_vals.get('partner_id'):
            input = line[field]
            part_obj = self.pool['res.partner']
            dom = ['|', ('parent_id', '=', False), ('is_company', '=', True)]
            dom_ref = dom + [('ref', '=', input)]
            partner_ids = part_obj.search(cr, uid, dom_ref, context=context)
            if not partner_ids:
                dom_name = dom + [('name', '=', input)]
                partner_ids = part_obj.search(
                    cr, uid, dom_name, context=context)
            if not partner_ids:
                msg = _("Partner '%s' not found !") % input
                self._log_line_error(line, msg)
                return
            elif len(partner_ids) > 1:
                msg = _("Multiple partners with Reference "
                        "or Name '%s' found !") % input
                self._log_line_error(line, msg)
                return
            else:
                aml_vals['partner_id'] = partner_ids[0]

    def _handle_product(self, cr, uid,
                        field, line, move, aml_vals, context=None):
        if not aml_vals.get('product_id'):
            input = line[field]
            prod_obj = self.pool['product.product']
            product_ids = prod_obj.search(
                cr, uid, [('default_code', '=', input)], context=context)
            if not product_ids:
                product_ids = prod_obj.search(
                    cr, uid, [('name', '=', input)], context=context)
            if not product_ids:
                msg = _("Product '%s' not found !") % input
                self._log_line_error(line, msg)
                return
            elif len(product_ids) > 1:
                msg = _("Multiple products with Internal Reference "
                        "or Name '%s' found !") % input
                self._log_line_error(line, msg)
                return
            else:
                aml_vals['product_id'] = product_ids[0]

    def _handle_date_maturity(self, cr, uid,
                              field, line, move, aml_vals, context=None):
        if not aml_vals.get('date_maturity'):
            due = line[field]
            try:
                datetime.strptime(due, '%Y-%m-%d')
                aml_vals['date_maturity'] = due
            except:
                msg = _("Incorrect data format for field '%s' "
                        "with value '%s', "
                        " should be YYYY-MM-DD") % (field, due)
                self._log_line_error(line, msg)

    def _handle_currency(self, cr, uid,
                         field, line, move, aml_vals, context=None):
        if not aml_vals.get('currency_id'):
            name = line[field]
            curr_ids = self.pool['res.currency'].search(
                cr, uid, [('name', '=ilike', name)], context=context)
            if curr_ids:
                aml_vals['currency_id'] = curr_ids[0]
            else:
                msg = _("Currency '%s' not found !") % name
                self._log_line_error(line, msg)

    def _handle_tax_code(self, cr, uid,
                         field, line, move, aml_vals, context=None):
        if not aml_vals.get('tax_code_id'):
            input = line[field]
            tc_obj = self.pool['account.tax.code']
            tc_ids = tc_obj.search(
                cr, uid, [('code', '=', input)], context=context)
            if not tc_ids:
                tc_ids = tc_obj.search(
                    cr, uid, [('name', '=', input)], context=context)
            if not tc_ids:
                msg = _("%s '%s' not found !") % (field, input)
                self._log_line_error(line, msg)
                return
            elif len(tc_ids) > 1:
                msg = _("Multiple %s entries with Code "
                        "or Name '%s' found !") % (field, input)
                self._log_line_error(line, msg)
                return
            else:
                aml_vals['tax_code_id'] = tc_ids[0]

    def _handle_analytic_account(self, cr, uid,
                                 field, line, move, aml_vals, context=None):
        if not aml_vals.get('analytic_account_id'):
            ana_obj = self.pool['account.analytic.account']
            input = line[field]
            domain = [('type', '!=', 'view'),
                      ('company_id', '=', move.company_id.id),
                      ('state', 'not in', ['close', 'cancelled'])]
            analytic_account_ids = ana_obj.search(
                cr, uid, domain + [('code', '=', input)], context=context)
            if len(analytic_account_ids) == 1:
                aml_vals['analytic_account_id'] = analytic_account_ids[0]
            else:
                analytic_account_ids = ana_obj.search(
                    cr, uid, domain + [('name', '=', input)], context=context)
                if len(analytic_account_ids) == 1:
                    aml_vals['analytic_account_id'] = analytic_account_ids[0]
            if not analytic_account_ids:
                msg = _("Invalid Analytic Account '%s' !") % input
                self._log_line_error(line, msg)
            elif len(analytic_account_ids) > 1:
                msg = _("Multiple Analytic Accounts found "
                        "that match with '%s' !") % input
                self._log_line_error(line, msg)

    def _process_line_vals(self, cr, uid,
                           line, move, aml_vals, context=None):
        """
        Use this method if you want to check/modify the
        line input values dict before calling the move write() method
        """
        if 'name' not in aml_vals:
            aml_vals['name'] = '/'

        if 'debit' not in aml_vals:
            aml_vals['debit'] = 0.0

        if 'credit' not in aml_vals:
            aml_vals['credit'] = 0.0

        all_fields = self._field_methods
        required_fields = [x for x in all_fields
                           if all_fields[x].get('required')]
        for rf in required_fields:
            if rf not in aml_vals:
                msg = _("The '%s' field is a required field "
                        "that must be correctly set.") % rf
                self._log_line_error(line, msg)

    def _process_vals(self, cr, uid, move, vals, context=None):
        """
        Use this method if you want to check/modify the
        input values dict before calling the move write() method
        """
        dp = self.pool['decimal.precision'].precision_get(cr, uid, 'Account')
        if round(self._sum_debit, dp) != round(self._sum_credit, dp):
            self._err_log += '\n' + _(
                "Error in CSV file, Total Debit (%s) is "
                "different from Total Credit (%s) !"
                ) % (self._sum_debit, self._sum_credit) + '\n'
        return vals

    def aml_import(self, cr, uid, ids, context=None):

        account_obj = self.pool.get('account.account')
        move_obj = self.pool.get('account.move')

        wiz = self.browse(cr, uid, ids[0], context=context)
        self._csv_separator = wiz.csv_separator
        self._decimal_separator = wiz.decimal_separator
        self._err_log = ''

        move = move_obj.browse(
            cr, uid, context['active_id'], context=context)
        account_ids = account_obj.search(
            cr, uid,
            [('type', 'not in', ['view', 'consolidation', 'closed']),
             ('company_id', '=', move.company_id.id)],
            context=context)
        accounts = account_obj.browse(
            cr, uid, account_ids, context=context)
        self._accounts_dict = {}
        for a in accounts:
            self._accounts_dict[a.code] = a.id
        self._sum_debit = self._sum_credit = 0.0
        self._get_orm_fields(cr, uid, context=context)
        lines, header, dialect = self._compute_lines(
            cr, uid, wiz.aml_data, context=context)

        header_fields = csv.reader(
            StringIO.StringIO(header), dialect=dialect).next()
        self._header_fields = self._process_header(
            cr, uid, header_fields, context=context)
        reader = csv.DictReader(
            StringIO.StringIO(lines), fieldnames=self._header_fields,
            dialect=dialect)

        inv_lines = []
        for line in reader:

            aml_vals = {}

            for i, hf in enumerate(self._header_fields):
                if i == 0 and line[hf] and line[hf][0] == '#':
                    # lines starting with # are considered as comment lines
                    break
                if hf in self._skip_fields:
                    continue
                if line[hf] == '':
                    continue

                try:
                    line[hf] = line[hf].decode(wiz.codepage).strip()
                except:
                    tb = ''.join(format_exception(*exc_info()))
                    raise Warning(
                        _("Wrong Code Page"),
                        _("Error while processing line '%s' :\n%s")
                        % (line, tb))

                if self._field_methods[hf].get('orm_field'):
                    self._field_methods[hf]['method'](
                        cr, uid, hf, line, move, aml_vals,
                        orm_field=self._field_methods[hf]['orm_field'],
                        context=context)
                else:
                    self._field_methods[hf]['method'](
                        cr, uid, hf, line, move, aml_vals, context=context)

            if aml_vals:
                self._process_line_vals(
                    cr, uid, line, move, aml_vals, context=context)
                inv_lines.append(aml_vals)

        vals = [(0, 0, l) for l in inv_lines]
        vals = self._process_vals(cr, uid, move, vals, context=context)

        if self._err_log:
            wiz.write({'note': self._err_log})
            mod_obj = self.pool['ir.model.data']
            result_view = mod_obj.get_object_reference(
                cr, uid, 'account_move_import', 'aml_import_view_form_result')
            return {
                'name': _("Import File result"),
                'res_id': ids[0],
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'aml.import',
                'view_id': result_view[1],
                'target': 'new',
                'type': 'ir.actions.act_window',
            }
        else:
            move.write({'line_id': vals})
            return {'type': 'ir.actions.act_window_close'}


def str2float(amount, decimal_separator):
    if not amount:
        return 0.0
    try:
        if decimal_separator == '.':
            return float(amount.replace(',', ''))
        else:
            return float(amount.replace('.', '').replace(',', '.'))
    except:
        return False


def str2int(amount, decimal_separator):
    if not amount:
        return 0
    try:
        if decimal_separator == '.':
            return int(amount.replace(',', ''))
        else:
            return int(amount.replace('.', '').replace(',', '.'))
    except:
        return False
