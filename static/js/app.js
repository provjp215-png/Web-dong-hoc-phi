function formatVnd(n) {
  return n.toLocaleString("vi-VN") + " ₫";
}

/* Chọn đợt nộp: tự tính tổng, bắt buộc chọn theo thứ tự */
function initInstallmentPicker() {
  const rows = Array.from(document.querySelectorAll(".inst-row"));
  const checks = rows.map(r => r.querySelector(".inst-check"));
  const open = rows.filter(r => !r.classList.contains("done"));
  function refresh(changed) {
    // nếu chọn đợt sau thì tự chọn các đợt trước; bỏ đợt trước thì bỏ các đợt sau
    if (changed) {
      const idx = open.indexOf(changed.closest(".inst-row"));
      open.forEach((r, i) => {
        const c = r.querySelector(".inst-check");
        if (changed.checked && i < idx) c.checked = true;
        if (!changed.checked && i > idx) c.checked = false;
      });
    }
    let total = 0, count = 0;
    rows.forEach(r => {
      const c = r.querySelector(".inst-check");
      r.classList.toggle("checked", c.checked);
      if (c.checked) { total += parseInt(r.dataset.amount); count++; }
    });
    document.getElementById("selTotal").textContent = formatVnd(total);
    document.getElementById("selCount").textContent = count;
    document.getElementById("payBtn").disabled = count === 0;
  }
  checks.forEach(c => c.addEventListener("change", () => refresh(c)));
  if (open.length) { open[0].querySelector(".inst-check").checked = true; }
  refresh();
}

/* Sao chép nhanh số tài khoản / nội dung chuyển khoản */
document.addEventListener("click", e => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  navigator.clipboard.writeText(b.dataset.copy).then(() => {
    const old = b.innerHTML;
    b.innerHTML = '<i class="bi bi-check2"></i> Đã chép';
    setTimeout(() => (b.innerHTML = old), 1500);
  });
});
