(() => {
  const form = document.querySelector('#contactForm');
  const topic = document.querySelector('#topic');
  const message = document.querySelector('#message');
  const searchForm = document.querySelector('[data-support-search]');
  const searchInput = document.querySelector('#support-search-input');
  const feedback = document.querySelector('[data-support-search-feedback]');
  const topicButtons = [...document.querySelectorAll('[data-support-topic]')];

  if (!form || !topic || !message) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const selectTopic = (value, announce = true) => {
    const option = [...topic.options].find((item) => item.value === value);
    if (!option) return;
    topic.value = value;
    topic.dispatchEvent(new Event('change', { bubbles: true }));
    if (announce && feedback) feedback.textContent = `${option.text} selected. Add the details below.`;
    form.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
    window.setTimeout(() => message.focus({ preventScroll: true }), reducedMotion ? 0 : 450);
  };

  topicButtons.forEach((button) => {
    button.addEventListener('click', () => selectTopic(button.dataset.supportTopic));
  });

  if (!searchForm || !searchInput) return;
  searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = searchInput.value.trim().toLowerCase();
    if (!query) {
      feedback.textContent = 'Enter a product or issue to search support.';
      searchInput.focus();
      return;
    }

    const aliases = [
      { topic: 'Getreep', terms: 'getreep travel trip itinerary map budget email import' },
      { topic: 'VocalFrame', terms: 'vocalframe video camera recording script teleprompter score take' },
      { topic: 'Account access', terms: 'account login log in sign in password reset access locked' },
      { topic: 'Billing and subscriptions', terms: 'billing subscription payment refund charge purchase plan' },
      { topic: 'Privacy and data', terms: 'privacy data delete export security information' },
      { topic: 'Technical issue', terms: 'technical bug error crash broken app not working device browser' },
    ];
    const match = aliases.find((item) => item.terms.includes(query) || query.split(/\s+/).some((term) => term.length > 2 && item.terms.includes(term)));
    if (match) {
      selectTopic(match.topic);
      return;
    }
    topic.value = 'Something else';
    feedback.textContent = 'No exact topic matched. Describe the issue below and we will route it.';
    form.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
    window.setTimeout(() => message.focus({ preventScroll: true }), reducedMotion ? 0 : 450);
  });
})();
