const form = document.getElementById('checkout-form');
const error = document.getElementById('form-error');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  button.textContent = 'Opening secure checkout…';
  error.hidden = true;
  try {
    const response = await fetch('/contractor-check/v1/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ licenseNumber: document.getElementById('license').value.trim() }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please try again.');
    window.location.assign(result.checkoutUrl);
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    button.disabled = false;
    button.innerHTML = 'Get report <span>↗</span>';
  }
});
