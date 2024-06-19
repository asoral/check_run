import json
import datetime
import pytest

import frappe

from check_run.check_run.doctype.check_run.check_run import (
	get_check_run_settings,
	get_entries,
	check_for_draft_check_run,
)

year = datetime.date.today().year


@pytest.fixture
def cr():  # return draft check run
	cr_name = check_for_draft_check_run(
		company="Chelsea Fruit Co",
		bank_account="Primary Checking - Local Bank",
		payable_account="2110 - Accounts Payable - CFC",
	)
	cr = frappe.get_doc("Check Run", cr_name)
	cr.flags.in_test = True
	cr.posting_date = cr.end_date = datetime.date(year, 12, 31)
	cr.set_last_check_number()
	cr.set_default_payable_account()
	cr.save()

	# Need CR Settings in place to call get_entries
	crs = get_check_run_settings(cr)

	entries = get_entries(cr)
	for row in entries.get("transactions"):
		row["pay"] = False
	cr.transactions = frappe.as_json(entries.get("transactions"))
	cr.save()
	return cr


def test_get_entries(cr):
	crs = get_check_run_settings(cr)
	assert frappe.db.exists("Check Run Settings", crs)
	cr.transactions = frappe.utils.safe_json_loads(cr.transactions)
	assert len(cr.transactions) > 1
	# assert that each type of source document appears at least once
	assert any([doc.get("doctype") == "Purchase Invoice" for doc in cr.transactions])
	assert any([doc.get("doctype") == "Journal Entry" for doc in cr.transactions])
	assert any([doc.get("doctype") == "Expense Claim" for doc in cr.transactions])
	# assert that the invoice with installment payment schedule appears more than once
	assert len([doc.get("name") == f"ACC-PINV-{year}-00001 " for doc in cr.transactions]) > 1


def test_process_check_run_on_hold_invoice_error(cr):
	cr.transactions = frappe.utils.safe_json_loads(cr.transactions)
	# try to pay invoice on hold to raise error
	for row in cr.transactions:
		if row.get("party") == "Liu & Loewen Accountants LLP":
			row["pay"] = True
			row["mode_of_payment"] = "Credit Card"
	cr.transactions = frappe.as_json(cr.transactions)
	cr.flags.in_test = True
	cr.save()
	with pytest.raises(
		frappe.exceptions.ValidationError, match=f"Purchase Invoice ACC-PINV-{year}-00020 is on hold"
	):
		# cr.flags.in_test = True
		cr.process_check_run()


def test_process_check_run_on_hold_invoice_auto_release(cr):
	# Test Settings auto-release of on-hold invoices
	cr.transactions = frappe.utils.safe_json_loads(cr.transactions)
	for row in cr.transactions:
		if row.get("party") == "Liu & Loewen Accountants LLP":
			row["pay"] = True
			row["mode_of_payment"] = "Credit Card"
	cr.transactions = frappe.as_json(cr.transactions)
	cr.flags.in_test = True
	cr.save()

	crs = get_check_run_settings(cr)
	crs.automatically_release_on_hold_invoices = True
	crs.save()

	try:
		cr.process_check_run()
	except frappe.exceptions.ValidationError:
		pytest.fail("Error raised on Check Run process when should have passed.")


def test_return_excluded_in_check_run(cr):
	# Test for ValidationError when Check Run only includes a return transaction
	cr.transactions = frappe.utils.safe_json_loads(cr.transactions)
	for row in cr.transactions:
		if row.get("party") == "Cooperative Ag Finance" and row.get("amount") < 0:
			raise ValueError("Default Settings should exclude this invoice from appearing")


def test_return_included_in_check_run_error(cr):
	# Test for ValidationError when Check Run includes only a return transaction
	_transactions = get_entries(cr).get("transactions")
	settings = get_check_run_settings(cr)
	assert settings.allow_stand_alone_debit_notes == "No"
	settings.allow_stand_alone_debit_notes = "Yes"
	settings.save()
	cr.posting_date = cr.end_date = datetime.date(year, 12, 30)
	cr.transactions = ""
	transactions = get_entries(cr).get("transactions")
	assert transactions != _transactions
	for row in transactions:
		if row.get("party") == "Cooperative Ag Finance" and row.get("amount") < 0:
			row["pay"] = True
	cr.transactions = frappe.as_json(transactions)
	cr.flags.in_test = True
	cr.save()

	with pytest.raises(frappe.exceptions.ValidationError, match=f"Difference Amount must be zero"):
		cr.process_check_run()


def test_return_offset_other_amounts(cr):
	# Test for offset when return applied to other invoices and net amount to pay is > 0
	party = "Cooperative Ag Finance"

	cr.transactions = frappe.utils.safe_json_loads(cr.transactions)
	total = 0.0
	for row in cr.transactions:
		if row.get("party") == party:
			total += row["amount"]
			row["pay"] = True
	cr.transactions = frappe.as_json(cr.transactions)
	cr.flags.in_test = True
	cr.save()
	cr.process_check_run()

	pe = frappe.get_doc("Payment Entry", {"party": party, "check_run": cr.name})
	assert total == pe.paid_amount == 9000.00
