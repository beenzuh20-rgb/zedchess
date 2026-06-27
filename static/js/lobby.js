// static/js/lobby.js
console.log("ZedChess Lobby JS loaded");

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('playerSearch');
    const onlineList = document.querySelectorAll('.online-players li');
    const noResults = document.getElementById('noResults');

    function filterPlayers() {
        const query = searchInput.value.trim().toLowerCase();
        let visibleCount = 0;

        onlineList.forEach((item) => {
            const playerName = item.querySelector('.player-name')?.textContent.toLowerCase() || '';
            const match = playerName.includes(query);
            item.style.display = match ? '' : 'none';
            if (match) visibleCount += 1;
        });

        if (noResults) {
            noResults.style.display = query && visibleCount === 0 ? 'block' : 'none';
        }
    }

    if (searchInput) {
        searchInput.addEventListener('input', filterPlayers);
    }
});

// Auto-refresh lobby only when search is blank
setInterval(() => {
    const searchInput = document.getElementById('playerSearch');
    if (window.location.pathname === '/lobby' && (!searchInput || !searchInput.value.trim())) {
        location.reload();
    }
}, 8000);