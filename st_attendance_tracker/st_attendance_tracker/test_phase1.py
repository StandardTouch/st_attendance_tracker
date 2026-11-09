import frappe
from st_attendance_tracker.api import _get_team_members, _cascade_series_done
from frappe.utils import today

def run_tests():
    print("Running Phase 1 Tests...")
    
    # 1. Test _get_team_members
    # It should run the new ORM queries without the Redis cache wrapper and not crash.
    members = _get_team_members("Administrator")
    assert isinstance(members, list), "_get_team_members should return a list"
    print(f"✅ _get_team_members passed (returned {len(members)} members)")

    # 2. Test _cascade_series_done
    # We will create a mock Daily Work Log and Task Entry, then run the cascade.
    test_emp = frappe.db.get_value("Employee", {"status": "Active"})
    if not test_emp:
        print("⚠️ No active employee found, skipping cascade test.")
        return

    # Create dummy Daily Work Log
    log = frappe.get_doc({
        "doctype": "Daily Work Log",
        "employee": test_emp,
        "date": today(),
        "tasks": [{
            "description": "Test Task",
            "status": "Pending",
            "series_id": "TEST-SERIES-123"
        }]
    }).insert(ignore_permissions=True)
    
    try:
        # Run the refactored frappe.qb code
        _cascade_series_done("TEST-SERIES-123", today())
        
        # Verify the task was marked as 'Done'
        task_status = frappe.db.get_value("Task Entry", log.tasks[0].name, "status")
        assert task_status == "Done", f"Expected 'Done', got '{task_status}'"
        print("✅ _cascade_series_done passed (Task successfully updated via QB)")
        
    finally:
        # Cleanup
        frappe.delete_doc("Daily Work Log", log.name, force=True)
        frappe.db.commit()
    
    print("🎉 All Phase 1 tests passed successfully!")
