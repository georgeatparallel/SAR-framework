
document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('search-form');
    const resultsContainer = document.getElementById('results-container');
    const processingMessage = document.createElement('p');
    processingMessage.textContent = 'Processing...';

    form.addEventListener('submit', async function (event) {
        event.preventDefault();

        // Clear previous results and show processing message
        resultsContainer.innerHTML = '';
        resultsContainer.appendChild(processingMessage);

        const formData = new FormData(form);
        const query = formData.get('query');

        try {
            const response = await fetch('/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ query: query }),
            });

            // Hide processing message
            resultsContainer.innerHTML = '';

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            displayResults(data.conversation_history);

        } catch (error) {
            console.error('Error fetching search results:', error);
            resultsContainer.innerHTML = `<p>An error occurred: ${error.message}</p>`;
        }
    });

    function displayResults(conversationHistory) {
        if (!conversationHistory || conversationHistory.length === 0) {
            resultsContainer.innerHTML = '<p>No results found.</p>';
            return;
        }

        const list = document.createElement('ul');
        list.className = 'conversation-list';

        conversationHistory.forEach(item => {
            const listItem = document.createElement('li');
            const role = Object.keys(item)[0];
            const content = item[role].content;

            const roleElement = document.createElement('strong');
            roleElement.textContent = `${role.charAt(0).toUpperCase() + role.slice(1)}:`;

            const contentElement = document.createElement('div');
            // Use <pre> for pre-formatted text to respect newlines and spacing
            const preElement = document.createElement('pre');
preElement.style.whiteSpace = 'pre-wrap'; // Ensure long lines wrap
            preElement.textContent = content;
            contentElement.appendChild(preElement);

            listItem.appendChild(roleElement);
            listItem.appendChild(contentElement);
            list.appendChild(listItem);
        });

        resultsContainer.appendChild(list);
    }
});
