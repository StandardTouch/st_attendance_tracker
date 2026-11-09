import frappe
from frappe.utils import today, now_datetime

def run_tests():
    print("Running Phase 2 Tests...")
    test_emp = frappe.db.get_value("Employee", {"status": "Active"})
    if not test_emp:
        print("⚠️ No active employee found, skipping EOD test.")
        return

    # Create dummy Daily Work Log
    log = frappe.get_doc({
        "doctype": "Daily Work Log",
        "employee": test_emp,
        "date": today(),
        "login_time": "09:00:00",
        "tasks": [{
            "description": "Test EOD Task",
            "status": "Pending",
            "series_id": "TEST-SERIES-EOD"
        }]
    }).insert(ignore_permissions=True)
    
    try:
        # Simulate EOD Submit via ORM update directly!
        log.eod_submitted = 1
        log.logout_time = now_datetime().strftime("%H:%M:%S")
        log.tasks[0].status = "Done"
        log.tasks[0].actual_time = "1.0"
        
        # This single save() should natively trigger ALL EOD automation!
        log.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Verify side effects happened
        task_status = frappe.db.get_value("Task Entry", log.tasks[0].name, "status")
        assert task_status == "Done", f"Expected 'Done', got '{task_status}'"
        
        checkins = frappe.get_all("Employee Checkin", filters={"employee": test_emp, "log_type": "OUT", "time": ["between", [f"{today()} 00:00:00", f"{today()} 23:59:59"]]})
        assert len(checkins) > 0, "OUT checkin was not automatically created by controller!"
        
        print("✅ EOD automation successfully triggered via native DocType on_update!")
        
    finally:
        # Cleanup
        frappe.delete_doc("Daily Work Log", log.name, force=True)
        # Cleanup checkins
        for c in frappe.get_all("Employee Checkin", filters={"employee": test_emp, "log_type": "OUT"}):
            frappe.delete_doc("Employee Checkin", c.name, force=True)
        frappe.db.commit()
    
    print("🎉 All Phase 2 tests passed successfully!")
