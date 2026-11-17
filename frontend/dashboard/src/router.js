// Điều hướng bằng hash: #/exams, #/exams/12 ...
export function navigate(path) {
  window.location.hash = `#/${path}`;
}
