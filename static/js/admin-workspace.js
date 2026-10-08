(() => {
  const nav = document.querySelector('.navbar--workspace');
  const toggle = nav?.querySelector('.workspace-menu-toggle');
  const menu = nav?.querySelector('.navbar-nav');
  if (toggle && menu) {
    nav.classList.add('is-collapsible');
    toggle.hidden = false;
    const closeMenu = () => {
      menu.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', 'Open navigation');
    };
    toggle.addEventListener('click', () => {
      const open = menu.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.classList.contains('is-open')) {
        closeMenu();
        toggle.focus();
      }
    });
    document.addEventListener('click', event => {
      if (!nav.contains(event.target)) closeMenu();
    });
    window.matchMedia('(max-width: 860px)').addEventListener('change', closeMenu);
  }

  const search = document.getElementById('publishing-search');
  if (search) {
    search.closest('label').hidden = false;
    const rows = [...document.querySelectorAll('.publishing-row')];
    const empty = document.querySelector('.publishing-no-results');
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      let matches = 0;
      rows.forEach(row => {
        row.hidden = !row.querySelector('.publishing-copy').textContent.toLocaleLowerCase().includes(query);
        if (!row.hidden) matches += 1;
      });
      empty.hidden = matches > 0;
    });
  }

  document.querySelectorAll('[data-delete-title]').forEach(link => {
    link.addEventListener('click', event => {
      if (!window.confirm(`Delete "${link.dataset.deleteTitle}"? This cannot be undone.`)) {
        event.preventDefault();
      }
    });
  });
})();
