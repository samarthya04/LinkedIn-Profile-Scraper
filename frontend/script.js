document.getElementById('startScrape').addEventListener('click', async () => {
    const status = document.getElementById('status');
    status.textContent = 'Status: Scraping...';
    
    try {
        const response = await fetch('/api/start-scrape', { method: 'POST' });
        const data = await response.json();
        if (data.status === 'success') {
            status.textContent = 'Status: Scraping Complete!';
            displayProfiles(data.profiles);
        } else {
            status.textContent = `Status: Error - ${data.message}`;
        }
    } catch (error) {
        status.textContent = `Status: Error - ${error.message}`;
    }
});

async function loadProfiles() {
    const response = await fetch('/api/profiles');
    const data = await response.json();
    if (data.status === 'success') {
        displayProfiles(data.profiles);
    }
}

function displayProfiles(profiles) {
    const profileList = document.getElementById('profileList');
    profileList.innerHTML = '';
    profiles.forEach(profile => {
        const div = document.createElement('div');
        div.className = 'profile-item';
        div.innerHTML = `
            <span>${profile.name}</span>
            <a href="${profile.url}" target="_blank">View Profile</a>
        `;
        profileList.appendChild(div);
    });
}

// Load existing profiles on page load
window.onload = loadProfiles;