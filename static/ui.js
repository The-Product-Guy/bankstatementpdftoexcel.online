(function () {
    document.documentElement.classList.add('motion-ready');

    function revealAll(elements) {
        elements.forEach(function (element) {
            element.classList.add('is-visible');
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        const revealElements = Array.from(document.querySelectorAll('[data-reveal]'));
        if (!revealElements.length) return;

        const reduceMotion = window.matchMedia &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        if (reduceMotion || !('IntersectionObserver' in window)) {
            revealAll(revealElements);
            return;
        }

        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                entry.target.classList.add('is-visible');
                observer.unobserve(entry.target);
            });
        }, {
            rootMargin: '0px 0px -10% 0px',
            threshold: 0.12
        });

        revealElements.forEach(function (element, index) {
            element.style.transitionDelay = Math.min(index * 45, 240) + 'ms';
            observer.observe(element);
        });
    });
}());
