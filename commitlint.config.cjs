module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'header-max-length': [2, 'always', 100],
    'body-max-line-length': [0],
    'footer-max-line-length': [0],
    // Subjects often start with a name or an acronym (ADR 0002, RLS, MOWL).
    'subject-case': [0],
    'type-enum': [2, 'always', ['feat', 'fix', 'perf', 'revert', 'docs', 'chore', 'refactor', 'test', 'build', 'ci', 'style']],
  },
};
