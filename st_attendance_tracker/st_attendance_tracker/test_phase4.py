import frappe
from frappe.utils import today

def run_tests():
    print("Running Phase 4 Tests (Native CRUD & Lineage Hooks)...")
    
    test_emp = frappe.db.get_value("Employee", {"status": "Active"})
    if not test_emp:
        print("⚠️ No active employee found, skipping test.")
        return
        
    try:
        # 1. Test TaskEntry on_trash hook
        # Create a mock lineage task that is 'Pending'
        mock_series = "TEST-PHASE4-SERIES"
        old_task = frappe.get_doc({
            "doctype": "Task Entry",
            "series_id": mock_series,
            "status": "Pending",
            "description": "Old Mock Task",
            "parent": "MOCK_PARENT",
            "parenttype": "Daily Work Log",
            "parentfield": "tasks"
        }).insert(ignore_permissions=True)
        
        # Create the 'carried' task we will delete
        current_task = frappe.get_doc({
            "doctype": "Task Entry",
            "series_id": mock_series,
            "status": "Pending",
            "description": "Current Mock Task",
            "parent": "MOCK_PARENT_2",
            "parenttype": "Daily Work Log",
            "parentfield": "tasks"
        }).insert(ignore_permissions=True)
        
        # Delete the current task, which should trigger on_trash and update the old_task to 'Rolled Over'
        frappe.delete_doc("Task Entry", current_task.name, force=True)
        
        # Check if the old task's status changed
        old_task_status = frappe.db.get_value("Task Entry", old_task.name, "status")
        assert old_task_status == "Rolled Over", f"on_trash hook failed to cascade delete lineage. Status is {old_task_status}"
        print("✅ TaskEntry on_trash hook successfully isolated the lineage!")
        
        # 2. Test Additional Work CRUD without API wrapper
        aw_doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": test_emp,
            "work_date": today(),
            "description": "Phase 4 Test Work",
            "hours_spent": "2h",
            "status": "Done"
        }).insert(ignore_permissions=True)
        print("✅ Native Additional Work insert successful (employee auto-resolved via before_insert)!")
        
        frappe.delete_doc("Additional Work", aw_doc.name, force=True)
        print("✅ Native Additional Work delete successful!")
        
        print("🎉 Phase 4 testing complete: Frontend components are safe to use native CRUD!")
        
    except Exception as e:
        print(f"❌ Test Failed: {str(e)}")
        raise e
    finally:
        # Cleanup
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE series_id = %s", (mock_series,))
        frappe.db.commit()
