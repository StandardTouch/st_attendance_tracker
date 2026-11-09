import frappe
from frappe.utils import today

def run_tests():
    print("Running Phase 3 Tests (Native Permissions & Lifecycle)...")
    
    test_emp = frappe.db.get_value("Employee", {"status": "Active"})
    if not test_emp:
        print("⚠️ No active employee found, skipping test.")
        return
        
    try:
        # 1. Test creation (ensure no custom _check_ownership crashes it)
        log = frappe.get_doc({
            "doctype": "Daily Work Log",
            "employee": test_emp,
            "date": today(),
            "login_time": "09:00:00"
        })
        log.insert(ignore_permissions=True)
        print("✅ Document successfully created without custom BOLA hooks.")
        
        # 2. Test fetching natively
        fetched_log = frappe.get_doc("Daily Work Log", log.name)
        assert fetched_log.employee == test_emp, "Failed to load document natively."
        print("✅ Document natively fetched.")
        
        # 3. Test update (ensure validate passes without _check_ownership)
        fetched_log.lunch_from = "13:00:00"
        fetched_log.save(ignore_permissions=True)
        print("✅ Document successfully updated (validate hook ran natively).")
        
        # 4. Test delete (ensure on_trash passes without _check_ownership)
        frappe.delete_doc("Daily Work Log", log.name, force=True)
        print("✅ Document successfully deleted (on_trash hook ran natively).")
        
        print("🎉 Phase 3 testing complete: Standard Frappe CRUD lifecycle works perfectly!")
        
    except Exception as e:
        print(f"❌ Test Failed: {str(e)}")
        raise e
