# -*- coding: utf-8 -*-
# Copyright 2009-2017 Noviat.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    'name': 'CODA Import - ISO 20022 Payment Order Matching',
    'version': '8.0.2.0.0',
    'license': 'AGPL-3',
    'author': 'Noviat',
    'website': 'http://www.noviat.com',
    'category': 'Accounting & Finance',
    'complexity': 'normal',
    'summary': 'CODA Import - ISO 20022 Payment Order Matching',
    'depends': [
        'l10n_be_coda_advanced',
        'account_pain',
    ],
    'data': [
        'views/coda_bank_account.xml',
    ],
    'installable': False,
}
