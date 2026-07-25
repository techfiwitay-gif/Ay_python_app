(() => {
  const labels = [
    "Welcome to VocalFrame",
    "Choose your tone and pace",
    "Record with delivery cues",
    "Review progress across takes",
  ];

  document.querySelectorAll("[data-vocalframe-carousel]").forEach((carousel) => {
    const viewport = carousel.querySelector(".vocalframe-carousel-viewport");
    const slides = Array.from(carousel.querySelectorAll(".vocalframe-carousel-slide"));
    const dots = Array.from(carousel.querySelectorAll("[data-carousel-dot]"));
    const previous = carousel.querySelector("[data-carousel-prev]");
    const next = carousel.querySelector("[data-carousel-next]");
    const status = carousel.querySelector("[data-carousel-status]");
    let activeIndex = 0;
    let scrollFrame = null;

    const updateControls = (index) => {
      activeIndex = Math.max(0, Math.min(index, slides.length - 1));
      dots.forEach((dot, dotIndex) => {
        if (dotIndex === activeIndex) {
          dot.setAttribute("aria-current", "true");
        } else {
          dot.removeAttribute("aria-current");
        }
      });
      previous.disabled = activeIndex === 0;
      next.disabled = activeIndex === slides.length - 1;
      status.textContent = `Screenshot ${activeIndex + 1} of ${slides.length}: ${labels[activeIndex]}`;
    };

    const showSlide = (index) => {
      const nextIndex = Math.max(0, Math.min(index, slides.length - 1));
      viewport.scrollTo({
        left: slides[nextIndex].offsetLeft,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
      updateControls(nextIndex);
    };

    previous.addEventListener("click", () => showSlide(activeIndex - 1));
    next.addEventListener("click", () => showSlide(activeIndex + 1));
    dots.forEach((dot, index) => dot.addEventListener("click", () => showSlide(index)));

    viewport.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        showSlide(activeIndex - 1);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        showSlide(activeIndex + 1);
      }
    });

    viewport.addEventListener("scroll", () => {
      if (scrollFrame !== null) {
        cancelAnimationFrame(scrollFrame);
      }
      scrollFrame = requestAnimationFrame(() => {
        const closestIndex = slides.reduce((closest, slide, index) => {
          const distance = Math.abs(slide.offsetLeft - viewport.scrollLeft);
          const closestDistance = Math.abs(slides[closest].offsetLeft - viewport.scrollLeft);
          return distance < closestDistance ? index : closest;
        }, 0);
        updateControls(closestIndex);
        scrollFrame = null;
      });
    }, { passive: true });

    window.addEventListener("resize", () => showSlide(activeIndex));
    updateControls(0);
  });
})();
