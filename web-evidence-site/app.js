const form = document.getElementById('claim-form');
const error = document.getElementById('claim-error');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  button.textContent = 'Opening secure checkout…';
  error.hidden = true;
  try {
    const response = await fetch('https://api.aisoup.net/web-evidence/v1/checkout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ claim: document.getElementById('claim').value.trim() }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please try again.');
    window.location.assign(result.checkoutUrl);
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    button.disabled = false;
    button.textContent = 'Continue to $2 checkout ↗';
  }
});
