# First stage - build static resources using node. Required Docker CE >=17.06
FROM node:latest
RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app
COPY assets/ assets/
COPY static/ static/
COPY package.json package-lock.json /usr/src/app/
COPY webpack.config.js .
RUN npm install
RUN npm run build

# Second stage, copy over static resources and start the python
FROM python:3.7-alpine
RUN apk update && apk upgrade && apk add --no-cache bash git openssh zlib-dev jpeg-dev gcc musl-dev libmagic curl tar
RUN pip install pipenv
LABEL maintainer="martin@helmgast.se"
WORKDIR /usr/src/app
COPY --from=0 /usr/src/app/static/ static/
COPY Pipfile Pipfile.lock run.py /usr/src/app/
ENV FLASK_APP=run.py PIPENV_NOSPIN=1 PIPENV_COLORBLIND=1 PIPENV_YES=1
RUN pipenv install --system
RUN pipenv install --system --clear
#ENV PATH="/usr/src/app/.venv/bin:${PATH}"
#RUN pipenv graph
COPY tools/ tools/
COPY lore/ lore/
COPY plugins/ plugins/
COPY config.py config.py

RUN flask lang-compile

# provide from git or by Docker autobild
# ARG SOURCE_COMMIT=no_ver
# provide from git or by Docker autobild
# ARG SOURCE_BRANCH=no_branch
# ENV LORE_VERSION ${SOURCE_BRANCH}-${SOURCE_COMMIT}

# This format runs executable without bash/shell, faster
CMD ["flask","run"]