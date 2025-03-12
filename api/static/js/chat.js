document.addEventListener("DOMContentLoaded", function() {
    const textarea = document.getElementById('dynamicTextarea');
    const form = document.getElementById('dynamicForm');

    function adjustFormWidth() {
        // Get the computed width of the textarea
        const textareaWidth = window.getComputedStyle(textarea).width;
        // Set the form width to match the textarea width
        form.style.width = textareaWidth;
    }

    // Adjust the form width initially
    adjustFormWidth();

    // Adjust the form width whenever the textarea's size changes
    textarea.addEventListener('input', adjustFormWidth);
    window.addEventListener('resize', adjustFormWidth);
});
