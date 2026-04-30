module.exports = {
  testEnvironment: 'node',
  testMatch: ['**/tests/**/*.test.js'],
  collectCoverageFrom: [
    'electron/src/**/*.js',
    'src/**/*.js',
    '!**/node_modules/**',
    '!**/dist/**'
  ],
  coverageThreshold: {
    global: {
      branches: 10,
      functions: 10,
      lines: 10,
      statements: 10
    }
  },
  testTimeout: 10000,
  verbose: true,
  moduleNameMapper: {
    '^electron$': '<rootDir>/tests/mocks/electron.js'
  }
};
