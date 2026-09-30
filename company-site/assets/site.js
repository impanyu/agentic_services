const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

if (reducedMotion) {
  document.querySelectorAll(".reveal").forEach((element) => element.classList.add("is-visible"));
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.14 }
  );
  document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));
}

const contactForm = document.querySelector("#contact-form");
const contactStatus = document.querySelector("#contact-status");

if (contactForm && contactStatus) {
  contactForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!contactForm.reportValidity()) return;

    const button = contactForm.querySelector("button[type='submit']");
    const data = new FormData(contactForm);
    button.disabled = true;
    contactForm.setAttribute("aria-busy", "true");
    contactStatus.className = "form-status";
    contactStatus.textContent = "Sending…";

    try {
      const response = await fetch("/contact/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: data.get("name"),
          email: data.get("email"),
          subject: data.get("subject"),
          message: data.get("message"),
          company: data.get("company"),
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Message could not be sent.");

      contactForm.reset();
      contactStatus.className = "form-status form-status--success";
      contactStatus.textContent = result.status === "sent"
        ? `Message sent · ${result.messageId}`
        : `Message safely received · ${result.messageId}`;
    } catch (error) {
      contactStatus.className = "form-status form-status--error";
      contactStatus.textContent = error.message || "Message could not be sent. Please try again.";
    } finally {
      button.disabled = false;
      contactForm.removeAttribute("aria-busy");
    }
  });
}
