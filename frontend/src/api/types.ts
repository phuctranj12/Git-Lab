export type Visibility = 'PRIVATE' | 'INTERNAL'
export type GroupRole = 'VIEWER' | 'DEVELOPER' | 'MAINTAINER' | 'OWNER'
export type BuildStatus = 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED' | 'CANCELLED'
export type Level = 'NONE' | 'VIEWER' | 'DEVELOPER' | 'MAINTAINER' | 'OWNER' | 'ADMIN'

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Meta {
  app_name: string
  version: string
  web_base_url: string
  package_index_url: string
  internal_package_prefix: string
  git_ssh_host: string
  git_ssh_port: number
  git_ssh_user: string
  registry_allow_anonymous: boolean
}

export interface User {
  id: string
  username: string
  email: string
  full_name: string | null
  is_active: boolean
  is_system_admin: boolean
  last_login_at: string | null
  created_at: string
}

export interface UserBrief {
  id: string
  username: string
  full_name: string | null
  email: string
}

export interface Group {
  id: string
  name: string
  slug: string
  description: string | null
  visibility: Visibility
  created_at: string
  project_count: number
  member_count: number
  my_role: GroupRole | null
  can_manage: boolean
}

export interface GroupMember {
  user: UserBrief
  role: GroupRole
  created_at: string
}

export interface ProjectMember {
  user: UserBrief
  role: string
  source: 'group' | 'project'
  created_at: string | null
}

export interface Project {
  id: string
  group_slug: string
  group_name: string
  name: string
  slug: string
  full_path: string
  description: string | null
  language: 'PYTHON' | 'GENERIC'
  visibility: Visibility
  default_branch: string
  protect_default_branch: boolean
  package_name: string | null
  package_prefix_valid: boolean
  repository_size_bytes: number
  latest_commit_sha: string | null
  latest_release_version: string | null
  last_build_status: BuildStatus | null
  archived: boolean
  created_at: string
  updated_at: string
  clone_url: string
  install_command: string | null
  python_requires: string | null
  my_level: Level
  permissions: string[]
}

export interface BuildStep {
  name: string
  status: string
  duration_seconds?: number | null
}

export interface Artifact {
  filename: string
  size_bytes: number
  sha256: string
  published: boolean
}

export interface Build {
  id: string
  project_id: string
  project_path: string | null
  number: number
  trigger_type: 'PUSH' | 'TAG' | 'MANUAL'
  ref_name: string
  commit_sha: string
  requested_version: string | null
  status: BuildStatus
  publish_package: boolean
  runner_name: string | null
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
  error_code: string | null
  error_message: string | null
  artifacts: Artifact[]
  steps: BuildStep[]
  created_by_username: string | null
  created_at: string
}

export interface PackageFile {
  id: string
  filename: string
  file_type: 'WHEEL' | 'SDIST'
  size_bytes: number
  sha256: string
  python_requires: string | null
  uploaded_at: string
  download_url: string
}

export interface PackageVersion {
  id: string
  package_name: string
  normalized_name: string
  version: string
  git_tag: string | null
  commit_sha: string | null
  build_id: string | null
  build_number: number | null
  is_yanked: boolean
  yanked_reason: string | null
  yanked_at: string | null
  download_count: number
  metadata_json: Record<string, unknown>
  created_at: string
  files: PackageFile[]
}

export interface PackageSummary {
  package_name: string
  normalized_name: string
  project_path: string
  project_name: string
  description: string | null
  latest_version: string | null
  version_count: number
  download_count: number
  last_published_at: string | null
}

export interface SshKey {
  id: string
  title: string
  fingerprint: string
  key_type: string
  last_used_at: string | null
  created_at: string
}

export interface Token {
  id: string
  name: string
  token_prefix: string
  token_type: 'PERSONAL' | 'SERVICE' | 'RUNNER'
  scopes: string[]
  project_id: string | null
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
  created_at: string
}

export interface TokenCreated extends Token {
  token: string
}

export interface RefInfo {
  name: string
  kind: 'branch' | 'tag'
  commit_sha: string
  committed_at: string | null
  subject: string | null
}

export interface Commit {
  sha: string
  short_sha: string
  author_name: string
  author_email: string
  authored_at: string
  subject: string
}

export interface TreeEntry {
  name: string
  path: string
  type: 'blob' | 'tree' | 'commit'
  size: number | null
}

export interface AuditEntry {
  id: string
  user_id: string | null
  actor_type: string
  actor_name: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  ip_address: string | null
  user_agent: string | null
  request_id: string | null
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface Runner {
  id: string
  name: string
  hostname: string | null
  status: 'ONLINE' | 'OFFLINE' | 'DRAINING'
  labels: Record<string, unknown>
  version: string | null
  last_heartbeat_at: string | null
  max_concurrent_jobs: number
  current_jobs: number
  online: boolean
}

export interface Dashboard {
  my_projects: Project[]
  my_groups: { slug: string; name: string; role: GroupRole; project_count: number }[]
  recent_builds: Build[]
  failed_builds: Build[]
  recent_releases: {
    package_name: string
    version: string
    is_yanked: boolean
    project_path: string | null
    project_name: string
    created_at: string
  }[]
  popular_packages: { package_name: string; downloads: number; latest_version: string | null; project_name: string }[]
}
