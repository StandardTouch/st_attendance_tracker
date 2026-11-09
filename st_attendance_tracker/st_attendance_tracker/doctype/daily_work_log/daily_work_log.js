frappe.ui.form.on("Daily Work Log", {
	refresh(frm) {
		const open_checkout = frm.doc.morning_submitted && !frm.doc.eod_submitted;
		if (frm.is_new() || !open_checkout || frm.doc.checkout_unlocked) return;
		if (!frappe.user_roles.includes("HR Manager")) return;

		frm.add_custom_button(__("Unlock Late Checkout"), () => {
			frappe.prompt(
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					reqd: 1,
					default: frm.doc.unlock_request_reason || "",
				},
				(values) => {
					frappe.call({
						method: "st_attendance_tracker.api.unlock_late_checkout",
						type: "POST",
						args: { employee: frm.doc.employee, date: frm.doc.date, reason: values.reason },
						freeze: true,
						callback: () => {
							frappe.show_alert({ message: __("Checkout unlocked"), indicator: "green" });
							frm.reload_doc();
						},
					});
				},
				__("Unlock Late Checkout for {0}", [frm.doc.employee_name || frm.doc.employee]),
				__("Unlock")
			);
		}, __("Actions"));
	},
});
